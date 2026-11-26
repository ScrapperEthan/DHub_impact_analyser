"""Shared tool execution, prompt loading and evidence tracking for the three agents."""

import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
from threading import Thread
from time import monotonic
from typing import TypedDict, TypeVar
from uuid import uuid4

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from .config import MAX_RUN_TIMEOUT, Settings
from .llm import LLMError, create_client
from .source_utils import SourceError
from .sources import Sources
from .tools.registry import ToolDefinition

log = logging.getLogger(__name__)
PROMPT_DIR = Path(__file__).with_name("prompts")
MAX_RUN_TOOLS = 50
MAX_INVESTIGATOR_TOOLS = 8
MAX_DELEGATIONS = 50
TIME_LIMIT = "The run reached its time limit."
TOOL_LIMIT = f"The run reached the limit of {MAX_RUN_TOOLS} source tool calls."
T = TypeVar("T")


class RunDeadlineExceeded(TimeoutError):
    pass


def load_system_prompt(*names: str) -> str:
    return "\n\n".join(
        (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
        for filename in ("common.md", *names)
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
        raise LLMError("The model did not return a valid JSON object.") from None
    if not isinstance(value, dict):
        raise LLMError("The model response must be a JSON object.")
    return value


def run_deadline(settings: Settings) -> float:
    return monotonic() + min(settings.run_timeout, MAX_RUN_TIMEOUT)


def run_before_deadline(deadline: float, operation: Callable[..., T], /, *args, **kwargs) -> T:
    if monotonic() >= deadline:
        raise RunDeadlineExceeded(TIME_LIMIT)
    result: Future[T] = Future()

    def invoke() -> None:
        try:
            if monotonic() >= deadline:
                raise RunDeadlineExceeded(TIME_LIMIT)
            result.set_result(operation(*args, **kwargs))
        except BaseException as exc:
            result.set_exception(exc)

    # External reads can block inside an adapter. The caller owns all evidence and Trace writes.
    Thread(target=copy_context().run, args=(invoke,), daemon=True).start()
    try:
        value = result.result(timeout=max(0, deadline - monotonic()))
    except TimeoutError:
        if result.done() and monotonic() < deadline:
            raise
        raise RunDeadlineExceeded(TIME_LIMIT) from None
    if monotonic() >= deadline:
        raise RunDeadlineExceeded(TIME_LIMIT)
    return value


class State(TypedDict, total=False):
    requirement: str
    plan: dict | None
    demo_case: bool
    domain: list[dict]
    code: list[dict]


class AgentRuntime:
    def __init__(
        self, settings: Settings, sources: Sources, snapshot: dict, emit: Callable[[dict], None], *, deadline: float,
    ):
        self.sources = sources
        self.snapshot = snapshot
        self.emit = emit
        self.settings = settings
        self.offline = settings.llm.provider == "offline"
        self.client = None if self.offline else create_client(settings.llm)
        self.deadline = deadline
        self.evidence: dict[str, dict] = {}
        self.limits: list[str] = []
        self.tool_count = 0
        self.span_count = 0

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
            value = value.replace(key, "[redacted]")
        value = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[redacted]", value)
        value = re.sub(r"(?i)\b((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[redacted]", value)
        return value[:limit]

    def trace_input(self, arguments: dict, fields: tuple[str, ...]) -> dict:
        summary = {}
        for key in fields:
            value = arguments.get(key)
            if isinstance(value, str):
                summary[key] = self.safe_text(value, 180)
            elif isinstance(value, int):
                summary[key] = value
            elif isinstance(value, list):
                summary[key] = [self.safe_text(item, 80) for item in value[:8] if isinstance(item, str)]
        return summary

    @contextmanager
    def agent_span(self, agent: str, stage: str, parent: str, task: dict | None = None):
        span = self.new_span(agent)
        start = monotonic()
        meta = {"agent_id": agent, "stage": stage, "span_id": span, "parent_span_id": parent}
        if task:
            meta["input_summary"] = self.trace_input(task, ("question",))
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

    def record_evidence(self, evidence: dict, span: str) -> str:
        evidence_id = f"E{len(self.evidence) + 1}"
        self.evidence[evidence_id] = {
            **evidence,
            "evidence_id": evidence_id,
            "tool_span_id": span,
            "excerpt": self.safe_text(evidence["excerpt"], 12000),
        }
        return evidence_id

    def call_tool(
        self, agent: str, parent: str, tools: dict[str, ToolDefinition], name: str, arguments: dict, *, delegation: bool = False,
    ) -> dict:
        if not self.has_tool_budget():
            self.note(TIME_LIMIT if self.remaining() <= 0 else TOOL_LIMIT)
            return {"error": "budget_exhausted"}
        definition = tools.get(name)
        if definition is None:
            self.note("The model requested a tool outside this investigator's toolset.")
            return {"error": "unknown_tool"}
        if delegation:
            try:
                return definition.tool.invoke(arguments)
            except (TypeError, ValueError, ValidationError):
                self.note("The investigation assignment had invalid arguments.")
                return {"error": "invalid_arguments"}
        self.tool_count += 1
        if self.tool_count == MAX_RUN_TOOLS:
            self.note(TOOL_LIMIT)
        span = self.new_span("tool")
        start = monotonic()
        meta = {
            "agent_id": agent, "tool_name": name, "span_id": span, "parent_span_id": parent,
            "input_summary": self.trace_input(arguments, definition.trace_fields),
        }
        self.emit({**meta, "type": "tool.started", "status": "running"})
        output = {}
        error_code = None
        error_summary = None
        status = "succeeded"
        try:
            result = run_before_deadline(self.deadline, definition.tool.invoke, arguments)
            evidence = definition.evidence_factory(result) if definition.evidence_factory else None
            if evidence:
                evidence_id = self.record_evidence(evidence, span)
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
                self.note("Some repositories could not be searched.")
            return result
        except SourceError as exc:
            error_code = self.safe_text(str(exc.code), 80)
            error_summary = self.safe_text(str(exc), 240)
            status = "timeout" if "timeout" in error_code.lower() else "failed"
        except TimeoutError as exc:
            if isinstance(exc, RunDeadlineExceeded):
                self.note(TIME_LIMIT)
            error_code, error_summary, status = "timeout", "The tool call timed out.", "timeout"
        except (TypeError, ValueError, ValidationError):
            error_code, error_summary, status = "invalid_arguments", "The tool received invalid arguments.", "failed"
        except Exception as exc:
            log.warning("Tool %s failed (%s)", name, type(exc).__name__)
            error_code, error_summary, status = "tool_failed", "The tool call failed.", "failed"
        finally:
            self.emit({
                **meta, "type": "tool.finished", "status": status,
                "elapsed_ms": int((monotonic() - start) * 1000), "output_summary": output,
                **({"error_code": error_code, "error_summary": error_summary} if error_code else {}),
            })
        self.note(f"{name} · {error_summary}")
        return {"error": error_code, "message": error_summary}

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        try:
            result = run_before_deadline(self.deadline, self.client.chat, messages, tools, timeout=self.remaining())
        except RunDeadlineExceeded:
            self.note(TIME_LIMIT)
            raise LLMError(TIME_LIMIT) from None
        return result.message

    def tool_message(
        self, call: dict, invoke: Callable[[str, dict], dict] | None = None, *, note_invalid: bool = True,
    ) -> dict:
        if invoke is None:
            result = {"error": "budget_exhausted"}
        else:
            try:
                arguments = json_object(call["function"]["arguments"])
            except LLMError:
                if note_invalid:
                    self.note("The model returned tool arguments that could not be parsed.")
                result = {"error": "invalid_arguments"}
            else:
                result = invoke(call["function"]["name"], arguments)
        return {"role": "tool", "tool_call_id": call["id"], "content": as_json(result)}

    def chat_with_tools(
        self,
        agent: str,
        span: str,
        messages: list[dict],
        tools: dict[str, ToolDefinition],
    ) -> str | None:
        tool_schema = [convert_to_openai_tool(definition.tool) for definition in tools.values()]
        count = 0

        def invoke(name: str, arguments: dict) -> dict:
            return self.call_tool(agent, span, tools, name, arguments)

        for _ in range(MAX_INVESTIGATOR_TOOLS + 1):
            active_tools = tool_schema if count < MAX_INVESTIGATOR_TOOLS and self.has_tool_budget() else None
            message = self.chat(messages, active_tools)
            calls = message.get("tool_calls", [])
            if not calls:
                return message.get("content")
            messages.append(message)
            for call in calls:
                if count < MAX_INVESTIGATOR_TOOLS and self.has_tool_budget():
                    count += 1
                    messages.append(self.tool_message(call, invoke))
                else:
                    self.note("The investigator reached its tool call limit.")
                    messages.append(self.tool_message(call))
        raise LLMError("The investigator reached its model turn limit.")

    def investigate(
        self,
        agent: str,
        span: str,
        stage: str,
        system_prompt: str,
        tools: dict[str, ToolDefinition],
        data: dict,
    ) -> dict:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": as_json(data)},
        ]
        try:
            return json_object(self.chat_with_tools(agent, span, messages, tools))
        except LLMError as exc:
            self.note(f"{stage} · {exc}")
        return {"open_questions": [f"Investigation incomplete. See the trace for {stage.lower()}."]}
