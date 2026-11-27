import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from dhub.agents.runtime import AgentRuntime, run_deadline
from dhub.config import Settings
from dhub.sources import Sources
from dhub.sources.codegraph import CodeGraphIndex, CodeGraphResult, UnavailableCodeGraphAdapter, load_codegraph
from dhub.sources.repositories import Repository
from dhub.sources.utils import SourceError
from dhub.tools.code import CodeTools, MAX_CODE_READ_CHARS

SHA = "a" * 40
INDEX = CodeGraphIndex("fixture-provider", SHA, "index-1", ("python",))


class GraphProvider:
    def __init__(self, result, *, index=INDEX, on_query=None):
        self.result = result
        self.index = index
        self.on_query = on_query
        self.calls = 0

    def index_info(self, repo_id, *, deadline):
        return self.index

    def explore(self, *, repo_id, index, query, deadline):
        self.calls += 1
        if self.on_query:
            self.on_query(deadline)
        return self.result


def graph_tools(provider, files=None):
    repo = Repository("project", Path("."), "dev", synthetic=True, commit_sha=SHA, files=files or {})
    return CodeTools({repo.repo_id: repo}, provider)


class GraphContractTest(unittest.TestCase):
    def test_legacy_relation_provider_can_still_query_by_commit(self):
        provider = SimpleNamespace(
            indexed_commit=lambda repo_id: SHA,
            explore=Mock(return_value=[{"callee": "callee_a"}]),
        )
        page = graph_tools(provider).codegraph_explore("project", "find callee")
        provider.explore.assert_called_once_with(repo_id="project", commit_sha=SHA, query="find callee")
        self.assertEqual(page["results"], [{"callee": "callee_a"}])
        self.assertIsNone(page["index_id"])

    def test_relation_windows_preserve_capture_truncation_to_last_page(self):
        provider = GraphProvider(CodeGraphResult(INDEX, results=[{"caller": f"call_{n}"} for n in range(101)]))
        tools = graph_tools(provider)
        page = tools.codegraph_explore("project", "find callers", limit=30)
        result_id = page["result_id"]
        rows = []
        while True:
            rows.extend(page["results"])
            self.assertEqual((page["captured_count"], page["status"], page["truncated"]), (100, "partial", True))
            self.assertEqual((page["commit_sha"], page["index_id"]), (SHA, INDEX.index_id))
            if not page["has_more"]:
                self.assertIsNone(page["next_offset"])
                break
            page = tools.codegraph_read_result(result_id, offset=page["next_offset"], limit=30)
        self.assertEqual(rows, provider.result.results[:100])
        self.assertEqual(provider.calls, 1)
        with self.assertRaisesRegex(SourceError, "this run"):
            graph_tools(provider).codegraph_read_result(result_id)

    def test_unicode_text_windows_keep_provider_coverage(self):
        text = "定位函数⛄\r\n下一行。" * 15
        provider = GraphProvider(CodeGraphResult(INDEX, content=text, truncated=True, warnings=("upstream limit",)))
        tools = graph_tools(provider)
        page = tools.codegraph_explore("project", "locate the function", max_chars=11)
        result_id = page["result_id"]
        fragments = []
        while True:
            self.assertEqual(page["result_format"], "text")
            self.assertEqual(page["status"], "partial")
            self.assertEqual(page["warnings"], ["upstream limit"])
            self.assertEqual(page["captured_count"], len(text))
            fragments.append(page["content"])
            if not page["has_more"]:
                break
            page = tools.codegraph_read_result(result_id, offset=page["next_offset"], max_chars=11)
        self.assertEqual("".join(fragments), text)
        self.assertEqual(provider.calls, 1)

    def test_different_sha_and_same_sha_index_swap_are_rejected(self):
        provider = GraphProvider(CodeGraphResult(INDEX, results=[{"caller": "caller_a"}]))
        tools = graph_tools(provider)
        old = tools.codegraph_explore("project", "caller")
        provider.on_query = lambda deadline: setattr(provider, "index", replace(INDEX, index_id="index-2"))
        with self.assertRaises(SourceError) as changed:
            tools.codegraph_explore("project", "callee")
        self.assertEqual(changed.exception.code, "codegraph_index_changed")
        self.assertEqual(len(tools.graph_results), 1)
        self.assertEqual(tools.codegraph_read_result(old["result_id"]), old)
        provider.index = replace(INDEX, commit_sha="b" * 40)
        stale = graph_tools(provider)
        self.assertEqual(stale.get_repository_metadata("project")["codegraph_status"], "stale")
        with self.assertRaises(SourceError) as different_sha:
            stale.codegraph_explore("project", "callee")
        self.assertEqual(different_sha.exception.code, "codegraph_stale")
        self.assertEqual(provider.calls, 2)

    def test_late_result_cannot_enter_capture_and_bad_arguments_skip_query(self):
        clock = [100.0]
        provider = GraphProvider(
            CodeGraphResult(INDEX, results=[{"caller": "too_late"}]),
            on_query=lambda deadline: clock.__setitem__(0, deadline + 1),
        )
        with patch("dhub.sources.codegraph.monotonic", side_effect=lambda: clock[0]):
            tools = graph_tools(provider)
            definition = tools.toolset()["codegraph_explore"].tool
            for invalid in ({"limit": True}, {"root": "elsewhere"}):
                with self.assertRaises(ValidationError):
                    definition.invoke({"repo_id": "project", "query": "caller", **invalid})
            self.assertEqual(provider.calls, 0)
            with self.assertRaises(SourceError) as expired:
                definition.invoke({"repo_id": "project", "query": "caller"})
        self.assertEqual(expired.exception.code, "codegraph_timeout")
        self.assertEqual(provider.calls, 1)
        self.assertFalse(tools.graph_results)

    def test_trace_and_main_get_coverage_without_graph_content_or_handle(self):
        provider = GraphProvider(CodeGraphResult(
            INDEX, content="private graph output", status="partial", warnings=("physical index path",),
        ))
        code = graph_tools(provider)
        sources = Sources()
        settings = Settings()
        events = []
        runtime = AgentRuntime(settings, sources, sources.snapshot(), events.append, deadline=run_deadline(settings))
        result_id = []

        def investigate(agent, span, messages, tools):
            result = runtime.call_tool(agent, span, tools, "codegraph_explore", {
                "repo_id": "project", "query": "private graph query",
            })
            result_id.append(result["result_id"])
            self.assertEqual(result["content"], "private graph output")
            return '{"retrieval_coverage": ["invented"], "coverage": "source review pending"}'

        with patch.object(runtime, "chat_with_tools", side_effect=investigate):
            with runtime.agent_span("code", "Check callers", "main") as span:
                findings = runtime.investigate("code", span, "Check callers", "Source review", code.toolset(), {})
        coverage, = findings["retrieval_coverage"]
        self.assertEqual((coverage["tool"], coverage["status"], coverage["returned"]), ("codegraph_explore", "partial", 20))
        self.assertFalse(runtime.evidence)
        self.assertEqual(events[-1]["status"], "partial")
        trace = json.dumps(events)
        for private in ("private graph query", "private graph output", "physical index path", result_id[0]):
            self.assertNotIn(private, trace)


class AdapterFailureTest(unittest.TestCase):
    def metadata(self, provider):
        snapshot, = graph_tools(provider).repository_snapshots()
        return snapshot["codegraph_reason"], snapshot.get("codegraph_error", "")

    def test_load_init_lookup_and_missing_index_have_distinct_reasons(self):
        with self.assertRaises(SourceError) as missing_module:
            load_codegraph("dhub_missing_codegraph_adapter:create")
        self.assertEqual(missing_module.exception.code, "codegraph_adapter_load_failed")
        self.assertIn("No module named 'dhub_missing_codegraph_adapter'", str(missing_module.exception))
        with self.assertRaises(SourceError) as missing_factory:
            load_codegraph("json:create_adapter")
        self.assertEqual(missing_factory.exception.code, "codegraph_adapter_load_failed")
        with self.assertRaises(SourceError) as failing_factory:
            load_codegraph("json:loads")  # Calling the factory without arguments raises TypeError.
        self.assertEqual(failing_factory.exception.code, "codegraph_adapter_init_failed")

        self.assertEqual(self.metadata(UnavailableCodeGraphAdapter(failing_factory.exception))[0], "codegraph_adapter_init_failed")
        self.assertEqual(self.metadata(None)[0], "codegraph_not_configured")
        broken = Mock(index_info=Mock(side_effect=RuntimeError("index file missing token=abc")))
        with self.assertLogs("dhub.sources.codegraph", "WARNING"):
            reason, error = self.metadata(broken)
        self.assertEqual(reason, "codegraph_index_unavailable")
        self.assertIn("RuntimeError: index file missing token=[redacted]", error)
        self.assertEqual(self.metadata(GraphProvider(None, index=None))[0], "codegraph_unavailable")

    def test_tool_errors_reach_trace_and_main_with_a_recovery_hint(self):
        sources = Sources()
        settings = Settings()
        events = []
        runtime = AgentRuntime(settings, sources, sources.snapshot(), events.append, deadline=run_deadline(settings))
        returned = []

        def investigate(agent, span, messages, tools):
            returned.append(runtime.call_tool(agent, span, tools, "read_file", {"repo_id": "fixture-decision", "path": "nope.py"}))
            return '{"tool_errors": ["invented"]}'

        with patch.object(runtime, "chat_with_tools", side_effect=investigate):
            with runtime.agent_span("code", "Check source", "main") as span:
                findings = runtime.investigate("code", span, "Check source", "Source review", sources.code.toolset(), {})
        failure, = returned
        self.assertEqual((failure["error"], failure["tool"], failure["status"]), ("file_not_found", "read_file", "failed"))
        self.assertIn("list_files", failure["hint"])
        self.assertEqual(findings["tool_errors"], [{
            "tool": "read_file", "error": "file_not_found", "message": failure["message"], "status": "failed",
            "input": {"repo_id": "fixture-decision", "path": "nope.py"},
        }])
        finished = next(event for event in events if event["type"] == "tool.finished")
        self.assertEqual((finished["status"], finished["error_code"]), ("failed", "file_not_found"))


class EvidenceWindowTest(unittest.TestCase):
    def test_long_code_window_and_evidence_share_text_and_source_coordinates(self):
        line = "password=x " * 1_250 + "required_condition = True"
        code = graph_tools(None, {"script.py": f"{line}\r\ncontinue_here()"})
        sources = Sources()
        settings = Settings()
        runtime = AgentRuntime(settings, sources, sources.snapshot(), lambda _: None, deadline=run_deadline(settings))
        args = {"repo_id": "project", "path": "script.py", "start_line": 1, "end_line": 1}
        first = runtime.call_tool("code", "span", code.toolset(), "read_file", args)
        self.assertTrue(first["redacted"])
        self.assertEqual(first["next_column_offset"], MAX_CODE_READ_CHARS)
        evidence = runtime.evidence[first["evidence_ids"][0]]
        self.assertEqual(evidence["excerpt"], f"1  {first['lines'][0]['text']}")
        self.assertEqual(evidence["end_column_offset"], len(first["lines"][0]["text"]))
        self.assertTrue(evidence["line_truncated"])
        offset = line.index("required_condition")
        focused = runtime.call_tool("code", "span", code.toolset(), "read_file", {
            **args, "column_offset": offset, "max_chars": 100,
        })
        focused_evidence = runtime.evidence[focused["evidence_ids"][0]]
        self.assertEqual(focused_evidence["excerpt"], "1  required_condition = True")
        self.assertEqual(focused_evidence["column_offset"], offset)
        self.assertEqual(focused_evidence["end_column_offset"], len(line))
