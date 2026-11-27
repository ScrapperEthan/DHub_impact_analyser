import json
import os
import subprocess
import unittest
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from dhub.agent_runtime import AgentRuntime, run_deadline
from dhub.agents.domain import DomainKnowledgeInvestigator
from dhub.config import Settings
from dhub.documents import Knowledge
from dhub.source_utils import SourceError
from dhub.sources import Sources
from dhub.tools.code import MAX_CODE_READ_CHARS
from dhub.tools.domain import DomainTools


class DocumentSearchTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def tools(self, *rows):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"documents": rows}), encoding="utf-8")
        return DomainTools(Knowledge(manifest))

    def page(self, page_id, text, *, version=1, title=None, feature_drop="FD2"):
        path = f"{page_id}-{version}.md"
        (self.root / path).write_bytes(text.encode("utf-8"))
        return {
            "page_id": page_id, "version": version, "title": title or page_id,
            "path": path, "feature_drop": feature_drop,
        }

    def test_repeated_headings_preamble_and_parent_windows(self):
        text = (
            "Preface statement.\r\n# Main\r\n## Rules\r\noriginal policy\r\n"
            "### Exceptions\r\nqualified override\r\n"
            "## Rules\r\nreplacement policy\r\n"
            "````markdown\r\n```\r\n# Literal heading in code\r\n````\r\n"
        )
        rows = (self.page("decision", text), self.page("plain", "Unheaded reference."))
        tools = self.tools(*rows)
        preamble = tools.search_documents("Preface statement")["results"][0]
        self.assertEqual((preamble["section"], preamble["match_line"]), ("Preamble", 1))
        plain = tools.search_documents("Unheaded reference")["results"][0]
        self.assertEqual((plain["section"], plain["match_line"]), ("Document", 1))

        current = tools.search_documents("replacement policy", page_id="decision", version=1)["results"][0]
        self.assertEqual((current["section"], current["start_line"], current["match_line"]), ("Rules", 7, 8))
        self.assertEqual(current["end_line"], 12)
        repeated = self.tools(*rows).search_documents("replacement policy")["results"][0]
        self.assertEqual(current["section_id"], repeated["section_id"])
        read = tools.read_document("decision", 1, section_id=current["section_id"], limit=20)
        self.assertTrue(read["truncated"])
        self.assertEqual((read["start_line"], read["end_line"]), (7, 8))
        rest = tools.read_document("decision", 1, section_id=current["section_id"], offset=read["next_offset"])
        self.assertEqual(read["content"] + rest["content"], text[text.rindex("## Rules"):])
        with self.assertRaisesRegex(SourceError, "Multiple sections share"):
            tools.read_document("decision", 1, section="Rules")

        previous = tools.search_documents("original policy")["results"][0]
        child = tools.search_documents("qualified override")["results"][0]
        self.assertNotEqual(previous["section_id"], current["section_id"])
        self.assertEqual(child["parent_section_id"], previous["section_id"])
        self.assertEqual((previous["end_line"], previous["read_end_line"]), (4, 6))
        self.assertIn("qualified override", tools.read_document("decision", 1, section_id=previous["section_id"])["content"])

    def test_relevance_and_explicit_page_version_scope(self):
        query = "frequency cap override"
        tools = self.tools(
            self.page("noisy", "# Activity\n" + "campaign cap " * 40, feature_drop="FD3"),
            self.page("decision", "## Frequency Cap Override\n" + "x" * 700 + f" {query} during emergency review\n"),
            self.page("decision", "## Updated\nA new frequency cap override.\n", version=2),
        )
        ranked = tools.search_documents(query, limit=1)
        self.assertEqual((ranked["status"], ranked["returned"], ranked["has_more"], ranked["truncated"]),
                         ("complete", 1, True, True))
        self.assertEqual((ranked["results"][0]["page_id"], ranked["results"][0]["version"]), ("decision", "1"))
        phrase = tools.search_documents("emergency review")["results"][0]
        self.assertEqual(phrase["match_line"], 2)
        self.assertIn("emergency review", phrase["snippet"])
        self.assertLessEqual(len(phrase["snippet"]), 380)
        scoped = tools.search_documents(query, page_id="decision", version=2, limit=1)
        self.assertEqual((scoped["results"][0]["version"], scoped["has_more"]), ("2", False))
        self.assertEqual(tools.search_documents(query, feature_drop="FD3")["results"][0]["page_id"], "noisy")

    def test_catalogue_paginates_and_reports_incomplete_snapshot(self):
        template = self.page("template", "# Sample\nCommon rules.")
        rows = [{**template, "page_id": str(index)} for index in range(275)]
        tools = self.tools(*rows, {**template, "page_id": "missing", "path": "missing.md"})
        offset, seen = 0, []
        while offset is not None:
            page = tools.list_documents(feature_drop="FD2", limit=50, offset=offset)
            self.assertEqual((page["status"], page["total"], page["returned"]), ("partial", 275, len(page["results"])))
            seen.extend(row["page_id"] for row in page["results"])
            offset = page["next_offset"]
            self.assertEqual(page["has_more"], offset is not None)
        self.assertEqual(seen, [str(index) for index in range(275)])
        incomplete = tools.search_documents("no such term", feature_drop="FD2")
        self.assertEqual((incomplete["status"], incomplete["returned"], incomplete["has_more"]), ("partial", 0, None))
        unavailable = DomainTools(Knowledge(None)).search_documents("cap")
        self.assertEqual((unavailable["status"], unavailable["has_more"]), ("unavailable", None))


@unittest.skipUnless(which("git"), "Git is required for the project search tests.")
class RepositorySearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        repo = Path(directory.name)

        def git(*args):
            subprocess.run([
                "git", "-C", str(repo), "-c", "user.name=DHub Test", "-c", "user.email=dhub@example.invalid",
                "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                "-c", f"core.hooksPath={repo / 'no-hooks'}", *args,
            ], check=True, capture_output=True, env=os.environ.copy())

        cls.long_line = "x" * (MAX_CODE_READ_CHARS + 500) + "\u2028AllowMandateCampaignOverride = True"
        files = {
            "a/noise.py": "x" * 2000 + "allow_mandate_campaign_override\n",
            "app/production.py": "x" * 2000 + "allow_mandate_campaign_override\n",
            "app/tests/alpha.py": "allow_mandate_campaign_override = False\n",
            "app/tests/beta.py": "ALLOW_MANDATE_CAMPAIGN_OVERRIDE = False\n",
            "app/tests/nested/deep.py": "allowMandateCampaignOverride = True\n",
            "app2/tests/other.py": "allowMandateCampaignOverride = False\n",
            "code/long line.py": f"\ufeff{cls.long_line}\r\nfollowing = True",
        }
        git("init", "-q")
        for name, content in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        git("add", "--", *files)
        git("commit", "-qm", "search fixture")
        cls.sources = Sources(data_mode="project", repositories=[{"repo_id": "local", "path": str(repo)}])

    def test_git_scopes_content_and_lists_matching_file_names(self):
        scope = {"path_prefix": "app", "glob": "**/tests/*.py"}
        with patch("dhub.repositories.MAX_GREP_BYTES", 500):
            result = self.sources.search_code("allow mandate campaign override", mode="identifier", limit=1, **scope)
        self.assertEqual((result["status"], result["returned"], result["has_more"], result["truncated"]),
                         ("complete", 1, True, True))
        self.assertEqual(result["results"][0]["matched_term"], "allow_mandate_campaign_override")
        literal = self.sources.search_code("allow mandate campaign override", **scope)
        self.assertEqual((literal["status"], literal["returned"], literal["has_more"]), ("complete", 0, False))
        multi = self.sources.search_code(
            queries=["not_found", "allow_mandate_campaign_override", "campaign_override"], **scope,
        )
        self.assertEqual(multi["returned"], 2)
        self.assertEqual({hit["path"] for hit in multi["results"]}, {"app/tests/alpha.py", "app/tests/beta.py"})
        self.assertEqual(self.sources.search_code(queries=["allow.*override"], **scope)["returned"], 0)
        exact = self.sources.search_code("ALLOW_MANDATE_CAMPAIGN_OVERRIDE", path_prefix="app/tests/beta.py", limit=1)
        self.assertEqual((exact["results"][0]["matched_term"], exact["has_more"]), ("ALLOW_MANDATE_CAMPAIGN_OVERRIDE", False))

        first = self.sources.list_files("local", limit=1, **scope)
        second = self.sources.list_files("local", limit=1, offset=first["next_offset"], **scope)
        self.assertEqual((first["total"], first["results"], second["results"], second["next_offset"]),
                         (2, [{"path": "app/tests/alpha.py"}], [{"path": "app/tests/beta.py"}], None))
        with self.assertRaises(SourceError):
            self.sources.search_code("override", glob="../*.py")

    def test_git_match_and_long_line_continuation_share_a_locator(self):
        result = self.sources.search_code("allow mandate campaign override", mode="identifier", path_prefix="code")
        hit, = result["results"]
        self.assertEqual((hit["line"], hit["matched_term"]), (1, "AllowMandateCampaignOverride"))
        self.assertIn(hit["matched_term"], hit["text"])
        self.assertLessEqual(len(hit["text"]), 400)
        args = {"repo_id": hit["repo_id"], "path": hit["path"]}
        first = self.sources.read_file(**args, end_line=1)
        self.assertTrue(first["truncated"])
        self.assertEqual((first["next_start_line"], first["next_column_offset"]), (1, MAX_CODE_READ_CHARS))
        rest = self.sources.read_file(**args, start_line=first["next_start_line"], end_line=1,
                                      column_offset=first["next_column_offset"])
        self.assertEqual(first["lines"][0]["text"] + rest["lines"][0]["text"], self.long_line)
        self.assertFalse(rest["truncated"])
        near = self.sources.read_file(**args, start_line=hit["line"], end_line=hit["line"], column_offset=hit["column"] - 1)
        self.assertTrue(near["lines"][0]["text"].startswith(hit["matched_term"]))
        self.assertEqual(near["commit_sha"], hit["commit_sha"])

    def test_incomplete_repo_search_reports_unknown_has_more(self):
        with patch("dhub.repositories.MAX_GREP_BYTES", 100):
            capped = self.sources.search_code("allow_mandate_campaign_override", path_prefix="a")
        self.assertEqual((capped["status"], capped["returned"], capped["has_more"]), ("partial", 0, None))
        self.assertIn("output byte limit", capped["warnings"][0])
        partial = self.sources.search_code(
            "allow_mandate_campaign_override", repo_ids=["local", "missing"], path_prefix="app/tests/beta.py",
        )
        self.assertEqual((partial["status"], partial["returned"], partial["has_more"]), ("partial", 1, None))
        self.assertEqual(partial["unavailable"][0]["repo_id"], "missing")
        with patch.object(self.sources.repositories["local"], "deadline", monotonic() - 1):
            expired = self.sources.search_code("override")
        self.assertEqual((expired["status"], expired["unavailable"][0]["code"]), ("unavailable", "source_timeout"))


class SearchRuntimeTest(unittest.TestCase):
    def test_search_history_and_section_evidence_reach_next_assignment(self):
        sources = Sources()
        settings = Settings()
        events = []
        runtime = AgentRuntime(settings, sources, sources.snapshot(), events.append, deadline=run_deadline(settings))
        investigator = DomainKnowledgeInvestigator(runtime)
        runtime.offline = False

        def request(name, **arguments):
            return {"content": None, "tool_calls": [{
                "id": name, "function": {"name": name, "arguments": json.dumps(arguments)},
            }]}

        def model(messages, tools):
            user_data = json.loads(messages[1]["content"])
            if user_data.get("search_history"):
                history, = user_data["search_history"]
                self.assertEqual((history["status"], history["scope"]["page_id"]), ("complete", "DEMO-FD2"))
                self.assertEqual(history["query"], "frequency cap")
                self.assertEqual(history["tool"], "search_documents")
                return {"content": '{"coverage": "Using prior search results."}'}
            last = messages[-1]
            if last["role"] == "user":
                return request("search_documents", query="frequency cap", page_id="DEMO-FD2")
            result = json.loads(last["content"])
            if last["tool_call_id"] == "search_documents":
                hit = next(hit for hit in result["results"] if hit["section"] == "Phase 5 / Step 18")
                return request("read_document", page_id=hit["page_id"], version=hit["version"], section_id=hit["section_id"])
            evidence_id, = result["evidence_ids"]
            return {"content": json.dumps({"evidence_ids": [evidence_id]})}

        state = {"requirement": "Review the frequency cap", "plan": None}
        with patch.object(runtime, "chat", side_effect=model):
            first = investigator.run(state, "main")
            follow_up = investigator.run({**state, "domain": [first]}, "main")
        self.assertIn("prior search", follow_up["coverage"])
        evidence = runtime.evidence[first["evidence_ids"][0]]
        self.assertEqual(evidence["page_id"], "DEMO-FD2")
        self.assertTrue(evidence["section_id"].startswith("sec-"))
        self.assertGreaterEqual(evidence["end_line"], evidence["start_line"])

    def test_partial_search_is_visible_in_tool_and_agent_trace(self):
        sources = Sources()
        settings = Settings()
        events = []
        runtime = AgentRuntime(settings, sources, sources.snapshot(), events.append, deadline=run_deadline(settings))
        tools = sources.code.toolset()
        for _ in range(2):
            with runtime.agent_span("code", "Check source", "main") as span:
                result = runtime.call_tool("code", span, tools, "search_code", {
                    "query": "unknown term", "repo_ids": ["fixture-decision", "unconfigured"],
                })
                self.assertEqual(result["status"], "partial")
        self.assertEqual(len(runtime.limits), 1)
        tool_ends = [event for event in events if event["type"] == "tool.finished"]
        self.assertEqual([event["status"] for event in tool_ends], ["partial", "partial"])
        self.assertEqual(tool_ends[0]["output_summary"]["returned"], 0)
        self.assertIsNone(tool_ends[0]["output_summary"]["has_more"])
        self.assertEqual([event["status"] for event in events if event["type"] == "agent.finished"], ["partial", "partial"])
