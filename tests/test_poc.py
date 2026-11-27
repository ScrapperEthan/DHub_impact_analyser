import json
import os
import subprocess
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from dhub.agent_runtime import TIME_LIMIT, TOOL_LIMIT, run_deadline
from dhub.analyzer import DEMO_REQUIREMENT, analyze
from dhub.app import create_app
from dhub.config import LLMConfig, Settings
from dhub.llm import ChatResult, LLMError, LocalResponses
from dhub.sources import Sources


PROMPT_DIR = Path(__file__).resolve().parent.parent / "dhub" / "prompts"
MAIN_TOOLS = {"investigate_code", "investigate_domain", "update_plan"}
ANALYSIS_TASKS = [
    ("investigate_code", "确认共享频控函数的豁免分支。"),
    ("investigate_domain", "核对 P5 Step 18 的豁免规则和渠道。"),
    ("investigate_code", "检查 Inbound 对共享函数的调用。"),
    ("investigate_domain", "补查豁免对应的配置范围。"),
]


class ChatGateway(HTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), ChatHandler)
        self.calls = []
        self.errors = []
        self.evidence_ids = {}

    def complete(self, path, authorization, payload):
        assert path == "/v1/chat/completions"
        assert authorization == "Bearer test-token"
        assert payload["model"] == "test-model"
        assert payload["stream"] is False
        assert payload["max_tokens"] == 1234

        messages = payload["messages"]
        user_data = json.loads(messages[1]["content"])
        assert user_data["requirement"] == DEMO_REQUIREMENT
        tools = {tool["function"]["name"]: tool["function"] for tool in payload.get("tools", [])}
        if "# Impact report" in messages[0]["content"]:
            self.assert_system_prompt(messages, "main-report.md")
            assert user_data["source_tool_budget"] == 50
            assert user_data["snapshot"]["knowledge"]["status"] == "available"
            return self.analyze(messages, tools)
        if tools:
            assert payload["tool_choice"] == "auto"
            if "read_document" in tools:
                self.assert_system_prompt(messages, "domain.md")
                assert set(tools) == {"search_documents", "list_documents", "read_document", "get_document_metadata"}
                name = "read_document"
                arguments = (
                    {"page_id": "DEMO-SETTINGS", "version": "1", "section": "Frequency Cap"}
                    if user_data["domain_findings"] else
                    {"page_id": "DEMO-FD2", "version": "1", "section": "Phase 5 / Step 18"}
                )
            else:
                self.assert_system_prompt(messages, "code.md")
                assert set(tools) == {
                    "get_repository_metadata", "search_code", "list_files", "read_file",
                    "codegraph_explore", "codegraph_read_result",
                }
                name = "read_file"
                file_path = "decision/inbound.py" if user_data["code_findings"] else "decision/frequency.py"
                arguments = {"repo_id": "fixture-decision", "path": file_path}
            prior_ids = {item for finding in user_data["domain_findings"] + user_data["code_findings"]
                         for item in finding["evidence_ids"]}
            assert prior_ids == set(self.evidence_ids.values()) - {
                self.evidence_ids.get(arguments.get("page_id") or arguments.get("path")),
            }
            assert user_data["task"]["question"]
            assert arguments.keys() <= tools[name]["parameters"]["properties"].keys()
            call_id = f"call-{name}"
            if messages[-1]["role"] != "tool":
                self.calls.append(f"request:{name}")
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }],
                }

            assistant_call = messages[-2]["tool_calls"][0]
            assert assistant_call["id"] == messages[-1]["tool_call_id"] == call_id
            assert json.loads(assistant_call["function"]["arguments"]) == arguments
            result = json.loads(messages[-1]["content"])
            assert not result.get("error"), result
            evidence_id, = result["evidence_ids"]
            self.evidence_ids[arguments.get("page_id") or arguments.get("path")] = evidence_id
            self.calls.append(f"result:{name}")
            return self._json_message({"coverage": ["已读原文"], "evidence_ids": [evidence_id]})

        raise AssertionError("Unexpected investigator toolset")

    def analyze(self, messages, tools):
        results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
        available_ids = [row["evidence_id"] for result in results for row in result["evidence"]]
        assert available_ids == list(self.evidence_ids.values())
        if len(results) < len(ANALYSIS_TASKS):
            assert set(tools) == MAIN_TOOLS
            name, question = ANALYSIS_TASKS[len(results)]
            self.calls.append(name)
            return {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": f"dispatch-{len(results)}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps({"question": question})},
                }],
            }
        if messages[-1]["role"] == "tool":
            self.calls.append("report:invalid")
            return {"role": "assistant", "content": "Incomplete format"}

        assert messages[-1]["role"] == "system" and "requested format" in messages[-1]["content"]
        assert not tools
        self.calls.append("report")
        return self._json_message({
            "report": {
                "requirement_summary": "Outbound 频控变更",
                "summary": "网关生成的影响报告",
                "current_support": {
                    "status": "change_required",
                    "evidence_ids": available_ids + ["E999"],
                },
                "impact_items": [{
                    "id": "process-1",
                    "kind": "process",
                    "target": "Frequency Cap",
                    "effect": "direct_change",
                    "status": "confirmed",
                    "reason": "核对了当前豁免规则",
                    "evidence_ids": available_ids,
                }],
            },
        })

    @staticmethod
    def assert_system_prompt(messages, name):
        assert messages[0]["role"] == "system"
        files = ("common.md", "glossary.md", "main-plan.md", name) if name in {"main-chat.md", "main-report.md"} else ("common.md", "glossary.md", name)
        expected = "\n\n".join(
            (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
            for filename in files
        )
        actual = messages[0]["content"]
        if name == "main-report.md":
            prompt, separator, schema = actual.partition("\n\nReport JSON Schema:\n")
            assert prompt == expected and separator
            assert "impact_items" in json.loads(schema)["properties"]
        else:
            assert actual == expected, name

    @staticmethod
    def _json_message(content):
        return {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}


class ChatHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            message = self.server.complete(self.path, self.headers.get("Authorization"), payload)
            body = {"choices": [{"message": message}], "usage": {"total_tokens": 5}}
            status = 200
        except (AssertionError, KeyError, ValueError, TypeError) as exc:
            self.server.errors.append(str(exc))
            body = {"error": "Invalid test request"}
            status = 500
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


class ConversationGateway(ChatGateway):
    def complete(self, path, authorization, payload):
        messages = payload["messages"]
        if "# Conversation" not in messages[0]["content"]:
            return super().complete(path, authorization, payload)

        self.assert_system_prompt(messages, "main-chat.md")
        assert path == "/v1/chat/completions" and authorization == "Bearer test-token"
        assert {tool["function"]["name"] for tool in payload["tools"]} == MAIN_TOOLS
        last = messages[-1]
        if last["content"] == "我给项目取名叫 Cedar。":
            self.calls.append("chat")
            return {"role": "assistant", "content": "记住了，项目名是 Cedar。"}
        if last["content"] == "刚才取的名字是什么？":
            assert messages[1:3] == [
                {"role": "user", "content": "我给项目取名叫 Cedar。"},
                {"role": "assistant", "content": "记住了，项目名是 Cedar。"},
            ]
            self.calls.append("chat:follow-up")
            return {"role": "assistant", "content": "刚才取的名字是 Cedar。"}

        if last["role"] == "user":
            assert last["content"] == DEMO_REQUIREMENT
            name = "investigate_domain"
            question = "核对 Outbound Mandatory Campaign 的 Frequency Cap 豁免规则。"
        else:
            assert last["role"] == "tool"
            result = json.loads(last["content"])
            source, = result["evidence"]
            if last["tool_call_id"] == "investigate_domain":
                assert source["page_id"] == "DEMO-FD2"
                assert "both Inbound and Outbound" in source["excerpt"]
                name = "investigate_code"
                question = "核对已查到的 Outbound Mandatory 豁免在代码中的分支。"
            else:
                assert last["tool_call_id"] == "investigate_code"
                assert source["path"] == "decision/frequency.py"
                assert "allow_mandate_campaign_override" in source["excerpt"]
                self.calls.append("chat:grounded")
                return {"role": "assistant", "content": "演示规则见 DEMO-FD2 的 P5 Step 18，实现见 decision/frequency.py。"}

        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": name,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps({"question": question}, ensure_ascii=False)},
        }]}


class ChatTest(unittest.TestCase):
    def test_offline_chat_page_and_context(self):
        with TemporaryDirectory() as directory, TestClient(create_app(Settings(db_path=Path(directory) / "runs.sqlite3"))) as client:
            self.assertIn('href="/chat"', client.get("/").text)
            page = client.get("/chat").text
            self.assertIn('lang="en"', page)
            self.assertIn('id="chat-form"', page)
            self.assertIn("What is Frequency Cap?", page)
            self.assertEqual(client.get("/api/health").json()["chat_mode"], "offline")
            self.assertEqual(client.post("/api/chat", json={"message": "  "}).status_code, 422)
            first = {"user": "What is Frequency Cap?"}
            response = client.post("/api/chat", json={"message": first["user"]})
            self.assertEqual(response.status_code, 200, response.text)
            first["assistant"] = response.json()["reply"]
            self.assertIn("DEMO-FD2", first["assistant"])

            follow_up = {"message": "What about Inbound?"}
            self.assertIn("The offline demo", client.post("/api/chat", json=follow_up).json()["reply"])
            response = client.post("/api/chat", json={**follow_up, "history": [first]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(set(response.json()), {"reply"})
            self.assertIn("both Inbound and Outbound", response.json()["reply"])
            response = client.post("/api/chat/stream", json={"message": "你好"})
            self.assertEqual(
                [json.loads(line)["type"] for line in response.iter_lines()],
                ["agent.started", "agent.finished", "chat.completed"],
            )

    def test_model_chat_continues_history_and_uses_original_investigators(self):
        with TemporaryDirectory() as directory, ConversationGateway() as gateway:
            thread = Thread(target=gateway.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            try:
                settings = Settings(
                    db_path=Path(directory) / "runs.sqlite3",
                    llm=LLMConfig(
                        provider="openai_chat", base_url=f"http://127.0.0.1:{gateway.server_port}/v1",
                        model="test-model", api_key="test-token", max_tokens=1234,
                    ),
                )
                with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost"}), TestClient(create_app(settings)) as client:
                    first = client.post("/api/chat", json={"message": "我给项目取名叫 Cedar。"})
                    self.assertEqual(first.json(), {"reply": "记住了，项目名是 Cedar。"})
                    response = client.post("/api/chat", json={
                        "message": "刚才取的名字是什么？",
                        "history": [{"user": "我给项目取名叫 Cedar。", "assistant": first.json()["reply"]}],
                    })
                    self.assertEqual(response.json(), {"reply": "刚才取的名字是 Cedar。"})
                    response = client.post("/api/chat", json={"message": DEMO_REQUIREMENT})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn("decision/frequency.py", response.json()["reply"])
                self.assertEqual(gateway.errors, [])
                self.assertEqual(gateway.calls, [
                    "chat", "chat:follow-up", "request:read_document", "result:read_document",
                    "request:read_file", "result:read_file", "chat:grounded",
                ])
            finally:
                gateway.shutdown()
                thread.join()


def tool_request(name, arguments, call_id=None):
    return {"id": call_id or name, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class AdaptivePlanningTest(unittest.TestCase):
    def test_both_modes_can_create_and_revise_a_plan_after_an_implicit_investigation(self):
        domain_task = {"id": "T1", "agent": "domain", "question": "核对当前规则", "status": "completed"}
        code_task = {"id": "T2", "agent": "code", "question": "检查共享的代码路径", "status": "in_progress"}
        follow_up = {"id": "T3", "agent": "code", "question": "核对部署环境", "status": "pending"}
        plan = {
            "goal": "核对频控影响", "scope": {"渠道": "Outbound"}, "candidate_steps": ["P5 Step 18"],
            "reason": "业务规则已核实，需核对共享实现。test-token", "tasks": [domain_task, code_task],
        }
        revised = {
            **plan, "reason": "共享路径需要结合部署环境复核。",
            "tasks": [domain_task, {**code_task, "status": "completed"}, follow_up],
        }
        checked = []

        def request_tool(name, arguments, index):
            return {"role": "assistant", "content": None, "tool_calls": [tool_request(name, arguments, str(index))]}

        def chat(messages, tools, *, timeout):
            prompt = messages[0]["content"]
            results = [json.loads(msg["content"]) for msg in messages if msg["role"] == "tool"]
            if "# Domain knowledge investigation" in prompt:
                self.assertIsNone(json.loads(messages[1]["content"])["plan"])
                if not results:
                    return ChatResult(request_tool("read_document", {
                        "page_id": "DEMO-FD2", "version": "1", "section": "Phase 5 / Step 18",
                    }, 1), {})
                return ChatResult(ChatGateway._json_message({"evidence_ids": results[-1]["evidence_ids"]}), {})
            if "# Code investigation" in prompt:
                handoff = json.loads(messages[1]["content"])
                self.assertEqual(handoff["plan"]["version"], 1)
                self.assertEqual(handoff["task"]["task_id"], "T2")
                self.assertEqual(handoff["domain_findings"], [{"evidence_ids": ["E1"]}])
                if not results:
                    return ChatResult(request_tool("read_file", {
                        "repo_id": "fixture-decision", "path": "decision/frequency.py",
                    }, 1), {})
                return ChatResult(ChatGateway._json_message({"evidence_ids": results[-1]["evidence_ids"]}), {})

            self.assertEqual({tool["function"]["name"] for tool in tools}, MAIN_TOOLS)
            self.assertIn("Documents can be outdated", prompt)
            self.assertIn("at the pinned Git commit", prompt)
            index = len(results)
            if index == 0:
                message = request_tool("investigate_domain", {"question": "确认频控业务规则"}, index)
            elif index == 1:
                self.assertEqual(results[-1]["findings"]["evidence_ids"], ["E1"])
                message = request_tool("update_plan", {**plan, "tasks": [domain_task, domain_task]}, index)
            elif index == 2:
                self.assertEqual(results[-1]["error"], "invalid_arguments")
                self.assertIsNone(results[-1]["plan"])
                message = request_tool("update_plan", plan, index)
            elif index == 3:
                self.assertEqual(results[-1]["plan"]["version"], 1)
                question = {"question": "核对两个渠道的共享实现"}
                message = {"role": "assistant", "content": None, "tool_calls": [
                    tool_request("investigate_code", question, "missing-task"),
                    tool_request("investigate_code", {**question, "task_id": "T99"}, "unknown-task"),
                    tool_request("investigate_code", {**question, "task_id": "T1"}, "wrong-agent"),
                    tool_request("investigate_code", {**question, "task_id": "T2"}, "valid-task"),
                ]}
            elif index == 7:
                self.assertEqual([result["error"] for result in results[-4:-1]], [
                    "task_id_required", "unknown_task_id", "task_agent_mismatch",
                ])
                delegation = results[-1]
                self.assertEqual(delegation["findings"]["evidence_ids"], ["E2"])
                self.assertEqual(delegation["evidence"][0]["task_id"], "T2")
                self.assertEqual(delegation["plan"]["version"], 2)
                self.assertEqual(delegation["plan"]["tasks"][1]["status"], "in_progress")
                self.assertEqual(delegation["plan"]["tasks"][1]["evidence_ids"], ["E2"])
                message = request_tool("update_plan", {
                    **revised, "tasks": [domain_task, {**code_task, "evidence_ids": ["E99"]}],
                }, index)
            elif index == 8:
                self.assertEqual(results[-1]["error"], "invalid_arguments")
                message = request_tool("update_plan", revised, index)
            else:
                self.assertEqual(results[-1]["plan"]["version"], 3)
                if messages[-1]["role"] == "system":
                    self.assertIn("Open task IDs", messages[-1]["content"])
                    checked.append(prompt)
                message = (
                    ChatGateway._json_message({"report": {"requirement_summary": "测试", "summary": "调查结果"}})
                    if "# Impact report" in prompt else {"role": "assistant", "content": "调查结果"}
                )
            return ChatResult(message, {})

        with TemporaryDirectory() as directory:
            settings = Settings(
                db_path=Path(directory) / "runs.sqlite3", llm=LLMConfig(provider="openai_chat", api_key="test-token"),
            )
            with patch("dhub.agent_runtime.create_client", return_value=SimpleNamespace(chat=chat)):
                with TestClient(create_app(settings)) as client:
                    response = client.post("/api/chat/stream", json={"message": DEMO_REQUIREMENT})
                    self.assertEqual(response.headers["content-type"], "application/x-ndjson")
                    chat_events = [json.loads(line) for line in response.iter_lines()]
                    run_id = client.post("/api/runs", json={"requirement": DEMO_REQUIREMENT}).json()["run_id"]
                    response = client.get(f"/api/runs/{run_id}/stream")
                    run_events = [json.loads(line) for line in response.iter_lines()]
                    run = client.get(f"/api/runs/{run_id}").json()

        self.assertEqual(len(checked), 2)
        self.assertEqual(chat_events[-1]["type"], "chat.completed")
        self.assertIn(follow_up["question"], chat_events[-1]["reply"])
        self.assertEqual(run_events[-1]["status"], "partial")
        self.assertIn(follow_up["question"], " ".join(run["report"]["open_questions"]))
        implicit, bound = run["report"]["evidence"]
        self.assertNotIn("task_id", implicit)
        self.assertEqual((bound["evidence_id"], bound["task_id"]), ("E2", "T2"))
        for events in (chat_events, run_events):
            plans = [event["plan"] for event in events if event["type"] == "plan.updated"]
            self.assertEqual([plan["version"] for plan in plans], [1, 2, 3, 4])
            self.assertEqual([len(plan["tasks"]) for plan in plans], [2, 2, 3, 3])
            self.assertEqual(plans[-1]["tasks"][-1]["status"], "blocked")
            self.assertEqual([plan["tasks"][1]["evidence_ids"] for plan in plans], [[], ["E2"], ["E2"], ["E2"]])
            self.assertTrue(all(not plan["tasks"][0]["evidence_ids"] for plan in plans))
            code_spans = [
                event for event in events if event["type"].startswith("agent.") and event["agent_id"] == "code"
            ]
            self.assertEqual([event["input_summary"]["task_id"] for event in code_spans], ["T2", "T2"])
            self.assertEqual(sum(event["type"] == "tool.started" for event in events), 2)
            order = [event.get("tool_name", event["type"]) for event in events]
            self.assertLess(order.index("read_document"), order.index("plan.updated"))
            self.assertLess(order.index("plan.updated"), order.index("read_file"))
            self.assertNotIn("test-token", json.dumps(events))


class LocalResponsesTest(unittest.TestCase):
    def test_dhub_model_override_and_endpoint_configuration(self):
        environment = {
            "LLM_PROVIDER": "local_responses", "LLM_MODEL": "shared-model", "DHUB_LLM_MODEL": "dhub-model",
            "LLM_MAX_TOKENS": "400", "DHUB_LLM_MAX_TOKENS": "500", "LLM_REASONING_EFFORT": "low",
        }
        with patch("dhub.config.os", SimpleNamespace(environ=environment)), patch("dhub.config.load_dotenv"):
            config = Settings.from_env().llm
        self.assertEqual((config.model, config.max_tokens, config.reasoning_effort), ("dhub-model", 500, "low"))
        self.assertEqual(str(LocalResponses(config).endpoint), "http://127.0.0.1:4141/v1/responses")
        for url, key, opt_in in (
            ("https://example.test/v1", "token", False),
            ("http://example.test/v1", "token", True),
            ("https://example.test/v1/responses", "", True),
        ):
            with self.subTest(url=url, has_key=bool(key), opt_in=opt_in), self.assertRaises(LLMError):
                LocalResponses(LLMConfig(base_url=url, api_key=key, allow_nonloopback=opt_in))

    def test_responses_round_trips_planned_delegation_and_read_evidence(self):
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "synthetic"}
        task = {"id": "T1", "agent": "domain", "question": "Verify the exemption", "status": "in_progress"}
        plan = {"goal": "Verify the Frequency Cap rule", "tasks": [task], "reason": "Check the source."}

        def call(name, call_id, arguments):
            return {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}

        def output(*items):
            return httpx.Response(200, json={"status": "completed", "output": items})

        def reply(text):
            return output({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]})

        def complete(request):
            self.assertEqual(str(request.url), "http://127.0.0.1:4141/v1/responses")
            payload = json.loads(request.content)
            self.assertEqual(payload["reasoning"], {"effort": "low"})
            self.assertEqual(payload["max_output_tokens"], 1000)
            self.assertFalse(payload["store"])
            self.assertNotIn("temperature", payload)
            self.assertEqual(payload["include"], ["reasoning.encrypted_content"])
            items = payload["input"]
            results = [item for item in items if item.get("type") == "function_call_output"]
            if "# Domain knowledge investigation" in items[0]["content"]:
                self.assertEqual(json.loads(items[1]["content"])["task"]["task_id"], "T1")
                if not results:
                    return output(reasoning, call("read_document", "read-1", {
                        "page_id": "DEMO-FD2", "version": "1", "section": "Phase 5 / Step 18",
                    }))
                self.assertIn(reasoning, items)
                self.assertEqual(results[-1]["call_id"], "read-1")
                self.assertEqual(json.loads(results[-1]["output"])["evidence_ids"], ["E1"])
                return reply(json.dumps({"evidence_ids": ["E1"]}))
            if not results:
                schemas = {tool["name"]: tool for tool in payload["tools"]}
                self.assertEqual(set(schemas), MAIN_TOOLS)
                schema = schemas["investigate_domain"]
                self.assertFalse(schema["strict"])
                self.assertIn("task_id", schema["parameters"]["properties"])
                self.assertEqual(schema["parameters"]["required"], ["question"])
                return output(
                    reasoning, call("update_plan", "plan-1", plan),
                    call("investigate_domain", "domain-1", {"task_id": "T1", "question": task["question"]}),
                )
            self.assertIn(reasoning, items)
            self.assertEqual([item["call_id"] for item in results[:2]], ["plan-1", "domain-1"])
            self.assertEqual(json.loads(results[1]["output"])["plan"]["tasks"][0]["evidence_ids"], ["E1"])
            if len(results) == 2:
                return output(call("update_plan", "plan-2", {**plan, "tasks": [{**task, "status": "completed"}]}))
            return reply("See E1 for the rule.")

        config = LLMConfig(provider="local_responses", model="test-model", reasoning_effort="low", max_tokens=1000)
        model = LocalResponses(config, transport=httpx.MockTransport(complete))
        with TemporaryDirectory() as directory:
            settings = Settings(db_path=Path(directory) / "runs.sqlite3", llm=config)
            with patch("dhub.agent_runtime.create_client", return_value=model), TestClient(create_app(settings)) as client:
                response = client.post("/api/chat/stream", json={"message": "What is Frequency Cap?"})
        events = [json.loads(line) for line in response.iter_lines()]
        self.assertEqual(events[-1]["type"], "chat.completed")
        self.assertEqual(events[-1]["reply"], "See E1 for the rule.")
        latest = [event["plan"] for event in events if event["type"] == "plan.updated"][-1]
        self.assertEqual(latest["tasks"][0], {**task, "status": "completed", "evidence_ids": ["E1"]})


class ProjectRepositoryTest(unittest.TestCase):
    @unittest.skipUnless(which("git"), "Git is required for the project repository test.")
    def test_single_dev_repository_pins_script_reads_and_covers_the_run(self):
        repo_id = "decision-engine-instant-decision-svc"
        with TemporaryDirectory() as directory:
            repo = Path(directory)

            def git(*args):
                return subprocess.check_output([
                    "git", "-C", directory, "-c", "user.name=DHub Test", "-c", "user.email=dhub@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={repo / 'no-hooks'}", *args,
                ], env=os.environ.copy(), text=True).strip()

            git("init", "-q", "-b", "main")
            scripts = {"run.sh": "# example_decision shell\n", "Deploy.PS1": "# example_decision powershell\n"}
            for name, text in scripts.items():
                (repo / name).write_text(text, encoding="utf-8")
            git("add", "--", *scripts)
            git("commit", "-qm", "dev script fixtures")
            dev_revision = git("rev-parse", "HEAD")
            git("branch", "dev")
            git("update-ref", "refs/remotes/origin/dev", dev_revision)
            (repo / "Deploy.PS1").write_text("# working branch\n", encoding="utf-8")
            git("commit", "-qam", "update main")

            manifest = PROMPT_DIR.parents[1] / "demo" / "manifest.json"
            repos = [{"repo_id": repo_id, "path": directory, "ref": "dev"}]
            codegraph = SimpleNamespace(indexed_commit=lambda _repo_id: dev_revision)
            sources = Sources(data_mode="project", manifest_path=manifest, repositories=repos, codegraph=codegraph)
            repo_snapshot, = sources.snapshot()["repositories"]
            self.assertEqual((repo_snapshot["branch"], repo_snapshot["commit_sha"]), ("dev", dev_revision))
            tracked, = Sources(
                data_mode="project", manifest_path=manifest, repositories=[{**repos[0], "ref": "origin/dev"}],
            ).snapshot()["repositories"]
            self.assertEqual((tracked["branch"], tracked["commit_sha"]), ("origin/dev", dev_revision))
            self.assertEqual(git("branch", "--show-current"), "main")
            matches = sources.search_code("example_decision", extensions=["sh", "ps1"])["results"]
            self.assertEqual({match["path"] for match in matches}, set(scripts))
            for name, text in scripts.items():
                source = sources.read_file(repo_id, name)
                self.assertEqual(source["lines"], [{"line": 1, "text": text.rstrip()}])
                self.assertEqual(source["commit_sha"], dev_revision)

            config = Settings(
                data_mode="project", manifest_path=manifest, repositories=repos,
                llm=LLMConfig(provider="openai_chat"),
            )
            response = {"report": {"requirement_summary": "Verify the scripts", "summary": "Fixture report"}}
            model = SimpleNamespace(chat=lambda *_args, **_kwargs: ChatResult(ChatGateway._json_message(response), {}))
            with patch("dhub.agent_runtime.create_client", return_value=model):
                with patch("dhub.analyzer.load_codegraph", return_value=codegraph):
                    status, report, detail = analyze(config, "Verify the scripts", lambda _: None, lambda _: None)
                    self.assertEqual((status, report["open_questions"], detail), ("succeeded", [], None))
                status, report, _ = analyze(config, "Verify the scripts", lambda _: None, lambda _: None)
                self.assertEqual(status, "partial")
                self.assertTrue(any("CodeGraph index" in gap for gap in report["open_questions"]))


class AgentBudgetTest(unittest.TestCase):
    def test_deadline_defaults_to_five_minutes_and_allows_shorter_overrides(self):
        with patch("dhub.agent_runtime.monotonic", return_value=10):
            self.assertEqual(run_deadline(Settings()), 310)
            self.assertEqual(run_deadline(Settings(run_timeout=3600)), 310)
            self.assertEqual(run_deadline(Settings(run_timeout=70)), 80)

    def test_fifty_source_tools_across_thirteen_delegations(self):
        delegations = 0
        refused = 0

        def chat(messages, tools, *, timeout):
            nonlocal delegations, refused
            if "# Impact report" in messages[0]["content"]:
                if not any(tool["function"]["name"] == "investigate_domain" for tool in tools or []):
                    self.assertEqual(refused, 2)
                    self.assertEqual({tool["function"]["name"] for tool in tools}, {"update_plan"})
                    return ChatResult(ChatGateway._json_message({"report": {
                        "requirement_summary": DEMO_REQUIREMENT, "summary": "已汇总限额内的调查范围。",
                    }}), {})
                delegations += 1
                calls = [tool_request("investigate_domain", {"question": "检查文档目录"}, f"delegate-{delegations}")]
            elif messages[-1]["role"] == "tool":
                refused += sum(json.loads(message["content"]).get("error") == "budget_exhausted" for message in messages[3:])
                return ChatResult(ChatGateway._json_message({"coverage": ["已检查目录"]}), {})
            else:
                calls = [tool_request("list_documents", {"limit": 1}, f"read-{index}") for index in range(4)]
            return ChatResult({"role": "assistant", "content": None, "tool_calls": calls}, {})

        events = []
        settings = Settings(llm=LLMConfig(provider="openai_chat"))
        with patch("dhub.agent_runtime.create_client", return_value=SimpleNamespace(chat=chat)):
            status, report, _ = analyze(settings, DEMO_REQUIREMENT, events.append, lambda snapshot: None)
        self.assertEqual(status, "partial")
        self.assertEqual(delegations, 13)
        self.assertEqual(len([event for event in events if event["type"] == "tool.started"]), 50)
        self.assertIn(TOOL_LIMIT, report["open_questions"])

    def test_run_timeout_preserves_evidence_during_a_blocked_model_call(self):
        script = iter([
            {"role": "assistant", "content": None, "tool_calls": [
                tool_request("investigate_code", {"question": "定位频控实现"}),
            ]},
            {"role": "assistant", "content": None, "tool_calls": [
                tool_request("read_file", {"repo_id": "fixture-decision", "path": "decision/frequency.py"}),
            ]},
            ChatGateway._json_message({"coverage": ["已查代码"], "evidence_ids": ["E1"]}),
        ])
        blocked, release = Event(), Event()

        def chat(messages, tools, *, timeout):
            response = next(script, None)
            if response is None:
                blocked.set()
                self.assertTrue(release.wait(5), "Analysis waited for a stalled gateway")
                response = {"role": "assistant", "content": "late response"}
            return ChatResult(response, {})

        settings = Settings(run_timeout=1, llm=LLMConfig(provider="openai_chat"))
        events = []
        try:
            with patch("dhub.agent_runtime.create_client", return_value=SimpleNamespace(chat=chat)):
                start = time.monotonic()
                status, report, _ = analyze(settings, DEMO_REQUIREMENT, events.append, lambda snapshot: None)
                self.assertLess(time.monotonic() - start, 3)
            self.assertTrue(blocked.is_set())
            self.assertEqual(status, "partial")
            self.assertIn(TIME_LIMIT, report["open_questions"])
            evidence, = report["evidence"]
            self.assertEqual(evidence["path"], "decision/frequency.py")
            self.assertEqual(events[-1]["type"], "agent.finished")
        finally:
            release.set()


class PoCTest(unittest.TestCase):
    def run_case(self, client):
        response = client.post("/api/runs", json={"requirement": DEMO_REQUIREMENT})
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()["run_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            snapshot = client.get(f"/api/runs/{run_id}").json()
            if snapshot["status"] in {"succeeded", "partial", "failed"}:
                events = client.get(f"/api/runs/{run_id}/events").json()["events"]
                return snapshot, events
            time.sleep(0.05)
        self.fail(f"Run did not reach terminal status: {run_id}")

    def assert_grounded_trace(self, report, events):
        self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[0]["type"], "run.started")
        self.assertEqual(events[-1]["type"], "run.finished")
        starts = {event["span_id"]: event for event in events if event["type"].endswith(".started")}
        finishes = {event["span_id"]: event for event in events if event["type"].endswith(".finished")}
        self.assertEqual(starts.keys(), finishes.keys())
        self.assertEqual({event["agent_id"] for event in events if event["type"] == "agent.started"},
                         {"main", "domain", "code"})
        for identifier, event in finishes.items():
            self.assertLess(starts[identifier]["seq"], event["seq"])
            if event["parent_span_id"]:
                self.assertIn(event["parent_span_id"], starts)
        for evidence in report["evidence"]:
            event = finishes[evidence["tool_span_id"]]
            self.assertEqual(event["type"], "tool.finished")
            self.assertEqual(event["status"], "succeeded")
            self.assertIn(evidence["evidence_id"], event["output_summary"]["evidence_ids"])

    def test_offline_run_and_replay(self):
        with TemporaryDirectory() as directory:
            settings = Settings(db_path=Path(directory) / "runs.sqlite3")
            with TestClient(create_app(settings)) as client:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn('lang="en"', page.text)
                self.assertIn("Run Analysis", page.text)
                self.assertEqual(client.get("/static/app.js").status_code, 200)
                snapshot, events = self.run_case(client)
            self.assertEqual(snapshot["status"], "succeeded", snapshot)
            self.assertEqual(snapshot["data_mode"], "demo")
            self.assertEqual(snapshot["source_snapshot"]["knowledge"]["document_count"], 2)
            self.assertEqual({repo["repo_id"] for repo in snapshot["source_snapshot"]["repositories"]},
                             {"fixture-decision", "fixture-channel"})
            report = snapshot["report"]
            self.assertEqual(report["current_support"]["status"], "change_required")
            self.assertTrue(any("P5 Step 18" in item["target"] for item in report["impact_items"]))
            self.assertEqual({entry["source_type"] for entry in report["evidence"]}, {"confluence", "code"})
            self.assertTrue(any(entry.get("page_id") == "DEMO-FD2" for entry in report["evidence"]))
            self.assertTrue(any(entry.get("path") == "decision/inbound.py" for entry in report["evidence"]))
            self.assert_grounded_trace(report, events)
            plans = [event["plan"] for event in events if event["type"] == "plan.updated"]
            self.assertEqual([plan["version"] for plan in plans], [1, 2, 3, 4])
            self.assertEqual([len(plan["tasks"]) for plan in plans], [2, 2, 4, 4])
            self.assertTrue(all(task["status"] == "completed" for task in plans[-1]["tasks"]))
            self.assertEqual([task["evidence_ids"] for task in plans[-1]["tasks"]], [
                ["E1", "E2"], ["E3", "E4", "E5"], ["E6", "E7"], ["E8"],
            ])
            trace = {event["span_id"]: event for event in events if event["type"].endswith(".finished")}
            evidence_by_id = {entry["evidence_id"]: entry for entry in report["evidence"]}
            for task in plans[-1]["tasks"]:
                for evidence_id in task["evidence_ids"]:
                    entry = evidence_by_id[evidence_id]
                    self.assertEqual(entry["task_id"], task["id"])
                    span = trace[entry["tool_span_id"]]["parent_span_id"]
                    self.assertEqual(trace[span]["input_summary"]["task_id"], task["id"])
            self.assertNotRegex(json.dumps([report, events], ensure_ascii=False), r"[\u3400-\u9fff]")
            tools = {event["tool_name"]: event for event in events if event["type"] == "tool.finished"}
            self.assertEqual(tools["codegraph_explore"]["output_summary"]["result_ref"],
                             tools["codegraph_read_result"]["output_summary"]["result_ref"])

            with TestClient(create_app(settings)) as client:
                base_url = f"/api/runs/{snapshot['run_id']}"
                self.assertEqual(client.get(base_url).json(), snapshot)
                replay = client.get(f"{base_url}/events", params={"after_seq": 5}).json()
                self.assertEqual(replay, {"events": events[5:], "next_after_seq": events[-1]["seq"]})
                streamed = client.get(f"{base_url}/stream", params={"after_seq": 5})
                self.assertEqual([json.loads(line) for line in streamed.iter_lines()], events[5:])
                drained = client.get(f"{base_url}/stream", params={"after_seq": events[-1]["seq"]})
                self.assertEqual(drained.content, b"")

    def test_openai_chat_native_tool_calls_and_report(self):
        with TemporaryDirectory() as directory, ChatGateway() as gateway:
            thread = Thread(target=gateway.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            try:
                settings = Settings(
                    db_path=Path(directory) / "runs.sqlite3",
                    llm=LLMConfig(
                        provider="openai_chat",
                        base_url=f"http://127.0.0.1:{gateway.server_port}/v1",
                        api_key="test-token",
                        model="test-model",
                        max_tokens=1234,
                    ),
                )
                with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost"}), TestClient(create_app(settings)) as client:
                    snapshot, events = self.run_case(client)
                self.assertEqual(gateway.errors, [])
                self.assertEqual(gateway.calls, [
                    "investigate_code", "request:read_file", "result:read_file",
                    "investigate_domain", "request:read_document", "result:read_document",
                    "investigate_code", "request:read_file", "result:read_file",
                    "investigate_domain", "request:read_document", "result:read_document",
                    "report:invalid", "report",
                ])
            finally:
                gateway.shutdown()
                thread.join()

        self.assertEqual(snapshot["status"], "succeeded", snapshot)
        report = snapshot["report"]
        self.assertEqual(report["summary"], "网关生成的影响报告")
        self.assertEqual(report["current_support"]["evidence_ids"], list(gateway.evidence_ids.values()))
        self.assertEqual({entry["source_type"] for entry in report["evidence"]}, {"confluence", "code"})
        self.assert_grounded_trace(report, events)
        delegates = [event for event in events if event["type"] == "agent.started" and event["agent_id"] != "main"]
        self.assertEqual([event["agent_id"] for event in delegates], ["code", "domain", "code", "domain"])
        self.assertEqual([event["input_summary"]["question"] for event in delegates], [question for _, question in ANALYSIS_TASKS])
        self.assertEqual([event["tool_name"] for event in events if event["type"] == "tool.finished"], [
            "read_file", "read_document", "read_file", "read_document",
        ])
        self.assertNotIn("test-token", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
