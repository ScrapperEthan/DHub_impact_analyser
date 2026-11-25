import json
import os
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from fastapi.testclient import TestClient

from dhub.analyzer import DEMO_REQUIREMENT
from dhub.app import create_app
from dhub.config import LLMConfig, Settings


PROMPT_DIR = Path(__file__).resolve().parent.parent / "dhub" / "prompts"


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
        if tools:
            assert payload["tool_choice"] == "auto"
            if "read_document" in tools:
                self.assert_system_prompt(messages, "domain.md")
                assert set(tools) == {"search_documents", "list_documents", "read_document", "get_document_metadata"}
                name = "read_document"
                arguments = {"page_id": "DEMO-FD2", "version": "1", "section": "Phase 5 / Step 18"}
            else:
                self.assert_system_prompt(messages, "code.md")
                assert set(tools) == {
                    "get_repository_metadata", "search_code", "read_file", "codegraph_explore", "codegraph_read_result",
                }
                assert user_data["domain_findings"][0]["evidence_ids"] == [self.evidence_ids["read_document"]]
                name = "read_file"
                arguments = {"repo_id": "fixture-decision", "path": "decision/frequency.py"}
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
            self.evidence_ids[name] = evidence_id
            self.calls.append(f"result:{name}")
            return self._json_message({"coverage": ["已读原文"], "evidence_ids": [evidence_id]})

        if "evidence" not in user_data:
            self.assert_system_prompt(messages, "main-plan.md")
            self.calls.append("plan")
            return self._json_message({"goal": DEMO_REQUIREMENT, "scope": ["Outbound"]})

        self.assert_system_prompt(messages, "main-report.md")
        self.calls.append("report")
        available_ids = [row["evidence_id"] for row in user_data["evidence"]]
        assert available_ids == list(self.evidence_ids.values())
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
        expected = "\n\n".join(
            (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
            for filename in ("common.md", name)
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
                self.assertEqual(client.get("/").status_code, 200)
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
            tools = {event["tool_name"]: event for event in events if event["type"] == "tool.finished"}
            self.assertEqual(tools["codegraph_explore"]["output_summary"]["result_id"],
                             tools["codegraph_read_result"]["input_summary"]["result_id"])

            with TestClient(create_app(settings)) as client:
                base_url = f"/api/runs/{snapshot['run_id']}"
                self.assertEqual(client.get(base_url).json(), snapshot)
                replay = client.get(f"{base_url}/events", params={"after_seq": 5}).json()
                self.assertEqual(replay, {"events": events[5:], "next_after_seq": events[-1]["seq"]})

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
                    "plan", "request:read_document", "result:read_document",
                    "request:read_file", "result:read_file", "report",
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
        self.assertNotIn("test-token", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
