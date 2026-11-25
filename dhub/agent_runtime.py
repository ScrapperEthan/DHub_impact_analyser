"""Shared tool execution, prompt loading and evidence tracking for the three agents."""

import json
import logging
import re
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import TypedDict
from uuid import uuid4

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from .config import Settings
from .llm import LLMError, create_client
from .sources import SourceError, Sources

log = logging.getLogger(__name__)
PROMPT_DIR = Path(__file__).with_name("prompts")
MAX_RUN_TOOLS = 24
MAX_INVESTIGATOR_TOOLS = 8
TRACE_ARGS = {
    "query", "feature_drop", "ancestor", "limit", "page_id", "version", "section", "offset",
    "repo_id", "repo_ids", "path_prefix", "path", "extensions", "start_line", "end_line", "result_id",
}


def load_system_prompt(name: str) -> str:
    return "\n\n".join(
        (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
        for filename in ("common.md", name)
    )


def as_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def json_object(content: str | None) -> dict:
    text = (content or "").strip()
    if text.startswith("```") and text.endswith("```") and "\n" in text:
        text = text.split("\n", 1)[1][:-3].strip()
    try:
        value = json.loads(text)
    except ValueError:
        raise LLMError("模型未返回可解析的 JSON 对象") from None
    if not isinstance(value, dict):
        raise LLMError("模型需返回 JSON 对象")
    return value


class State(TypedDict, total=False):
    requirement: str
    plan: dict
    domain: list[dict]
    code: list[dict]
    follow_up: dict | None
    followed_up: bool
    report: dict


class AgentRuntime:
    def __init__(self, settings: Settings, sources: Sources, snapshot: dict, emit: Callable[[dict], None]):
        self.sources = sources
        self.snapshot = snapshot
        self.emit = emit
        self.settings = settings
        self.offline = settings.llm.provider == "offline"
        self.client = None if self.offline else create_client(settings.llm)
        self.deadline = monotonic() + settings.run_timeout
        self.evidence: dict[str, dict] = {}
        self.limits: list[str] = []
        self.tool_count = 0
        self.span_count = 0

    def bind_tools(self, descriptions: dict[str, str]) -> dict[str, StructuredTool]:
        return {
            name: StructuredTool.from_function(
                func=getattr(self.sources, name), name=name, description=description
            )
            for name, description in descriptions.items()
        }

    def has_tool_budget(self) -> bool:
        return self.tool_count < MAX_RUN_TOOLS and self.remaining() > 0

    def note(self, text: str):
        if text not in self.limits:
            self.limits.append(text)

    def remaining(self) -> float:
        return self.deadline - monotonic()

    def new_span(self, prefix: str) -> str:
        self.span_count += 1
        return f"{prefix}-{self.span_count}-{uuid4().hex[:6]}"

    def safe_text(self, value: str, limit: int) -> str:
        key = self.settings.llm.api_key
        if key:
            value = value.replace(key, "[已隐藏]")
        value = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[已隐藏]", value)
        value = re.sub(r"(?i)\b((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[已隐藏]", value)
        return value[:limit]

    def trace_input(self, arguments: dict) -> dict:
        summary = {}
        for key in TRACE_ARGS.intersection(arguments):
            value = arguments[key]
            if isinstance(value, str):
                summary[key] = self.safe_text(value, 180)
            elif isinstance(value, int):
                summary[key] = value
            elif isinstance(value, list):
                summary[key] = [self.safe_text(item, 80) for item in value[:8] if isinstance(item, str)]
        return summary

    @contextmanager
    def agent_span(self, agent: str, stage: str, parent: str):
        span = self.new_span(agent)
        start = monotonic()
        meta = {"agent_id": agent, "stage": stage, "span_id": span, "parent_span_id": parent}
        previous_limits = len(self.limits)
        self.emit({**meta, "type": "agent.started", "status": "running"})
        status = "succeeded"
        try:
            yield span
        except Exception:
            status = "failed"
            raise
        finally:
            if status == "succeeded" and len(self.limits) > previous_limits:
                status = "partial"
            self.emit({**meta, "type": "agent.finished", "status": status, "elapsed_ms": int((monotonic() - start) * 1000)})

    def collect_evidence(self, name: str, result: dict, span: str) -> str | None:
        if name == "read_document" and result.get("content"):
            metadata = result["metadata"]
            fields = {key: metadata.get(key) for key in ("page_id", "version", "title", "feature_drop", "source_url")}
            fields["section"] = result.get("section") or "整页"
            if result.get("offset"):
                fields["section"] += f" · 字符偏移 {result['offset']}"
            fields["source_type"] = "confluence"
            excerpt = result["content"]
        elif name == "read_file" and result.get("lines"):
            fields = {key: result.get(key) for key in ("repo_id", "branch", "commit_sha", "path", "start_line", "end_line")}
            fields["source_type"] = "code"
            excerpt = "\n".join(f"{line['line']}  {line['text']}" for line in result["lines"])
        else:
            return None
        evidence_id = f"E{len(self.evidence) + 1}"
        self.evidence[evidence_id] = {
            **fields,
            "evidence_id": evidence_id,
            "tool_span_id": span,
            "excerpt": self.safe_text(excerpt, 12000),
        }
        return evidence_id

    def call_tool(
        self, agent: str, parent: str, tools: dict[str, StructuredTool], name: str, arguments: dict
    ) -> dict:
        if not self.has_tool_budget():
            self.note("调查达到运行预算。")
            return {"error": "budget_exhausted"}
        tool = tools.get(name)
        if tool is None:
            self.note("模型尝试调用调查任务权限外的工具。")
            return {"error": "unknown_tool"}
        self.tool_count += 1
        span = self.new_span("tool")
        start = monotonic()
        meta = {
            "agent_id": agent, "tool_name": name, "span_id": span, "parent_span_id": parent,
            "input_summary": self.trace_input(arguments),
        }
        self.emit({**meta, "type": "tool.started", "status": "running"})
        output = {}
        error_code = None
        error_summary = None
        status = "succeeded"
        try:
            result = tool.invoke(arguments)
            if self.remaining() <= 0:
                raise TimeoutError
            evidence_id = self.collect_evidence(name, result, span)
            if evidence_id:
                result = {**result, "evidence_ids": [evidence_id]}
                output["evidence_ids"] = [evidence_id]
            for key in ("results", "lines"):
                if isinstance(result.get(key), list):
                    output["count"] = len(result[key])
            for key in ("result_id", "next_offset", "next_start_line", "status"):
                if result.get(key) is not None:
                    value = result[key]
                    output[key] = self.safe_text(value, 180) if isinstance(value, str) else value
            if result.get("unavailable"):
                output["unavailable_count"] = len(result["unavailable"])
                self.note("部分仓库无法完成搜索。")
            return result
        except SourceError as exc:
            error_code = self.safe_text(str(exc.code), 80)
            error_summary = self.safe_text(str(exc), 240)
            status = "timeout" if "timeout" in error_code.lower() else "failed"
        except TimeoutError:
            error_code, error_summary, status = "timeout", "工具达到运行时限。", "timeout"
        except (TypeError, ValueError, ValidationError):
            error_code, error_summary, status = "invalid_arguments", "工具参数不符合约定。", "failed"
        except Exception as exc:
            log.warning("Tool %s failed (%s)", name, type(exc).__name__)
            error_code, error_summary, status = "tool_failed", "工具执行失败。", "failed"
        finally:
            self.emit({
                **meta, "type": "tool.finished", "status": status,
                "elapsed_ms": int((monotonic() - start) * 1000), "output_summary": output,
                **({"error_code": error_code, "error_summary": error_summary} if error_code else {}),
            })
        self.note(f"{name} · {error_summary}")
        return {"error": error_code, "message": error_summary}

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        if self.remaining() <= 0:
            raise LLMError("分析达到运行时限")
        result = self.client.chat(messages, tools, timeout=self.remaining())
        if self.remaining() <= 0:
            raise LLMError("分析达到运行时限")
        return result.message

    def ask(self, system_prompt: str, data: dict) -> dict:
        return json_object(self.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": as_json(data)},
        ]).get("content"))

    def investigate(
        self,
        agent: str,
        span: str,
        stage: str,
        system_prompt: str,
        tools: dict[str, StructuredTool],
        data: dict,
    ) -> dict:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": as_json(data)},
        ]
        tool_schema = [convert_to_openai_tool(tool) for tool in tools.values()]
        count = 0
        try:
            for _ in range(MAX_INVESTIGATOR_TOOLS + 1):
                active_tools = tool_schema if count < MAX_INVESTIGATOR_TOOLS and self.has_tool_budget() else None
                message = self.chat(messages, active_tools)
                calls = message.get("tool_calls", [])
                if not calls:
                    return json_object(message.get("content"))
                messages.append(message)
                for call in calls:
                    name = call["function"]["name"]
                    if count >= MAX_INVESTIGATOR_TOOLS or not self.has_tool_budget():
                        self.note("调查任务达到工具调用上限。")
                        result = {"error": "budget_exhausted"}
                    else:
                        count += 1
                        try:
                            arguments = json_object(call["function"]["arguments"])
                        except LLMError:
                            self.note("模型工具参数无法解析。")
                            result = {"error": "invalid_arguments"}
                        else:
                            result = self.call_tool(agent, span, tools, name, arguments)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": as_json(result)})
            self.note("调查任务达到模型轮数上限。")
        except LLMError as exc:
            self.note(f"{stage}受限 · {exc}")
        return {"open_questions": [f"{stage}尚未完成，见 Trace。"]}
