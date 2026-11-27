"""Shared tool execution, prompt loading and evidence tracking for the three agents."""

import json
import logging
import re
from collections import defaultdict, deque
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import copy_context
from hashlib import sha256
from pathlib import Path
from threading import Thread
from time import monotonic
from typing import TYPE_CHECKING, TypedDict, TypeVar
from uuid import uuid4

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from ..config import MAX_RUN_TIMEOUT, Settings
from ..llm import LLMError, create_client
from ..sources.utils import SourceError
from ..tools.registry import ToolDefinition

if TYPE_CHECKING:
    from ..sources.manager import Sources

log = logging.getLogger(__name__)
PROMPT_DIR = Path(__file__).parent.parent / "prompts"
MAX_RUN_TOOLS = 50
MAX_INVESTIGATOR_TOOLS = 8
MAX_DELEGATIONS = 50
SEARCH_TOOLS = {"search_code", "search_documents"}
GRAPH_TOOLS = {"codegraph_explore", "codegraph_read_result"}
MAX_SEARCH_HISTORY = 8
TIME_LIMIT = "The run reached its time limit."
TOOL_LIMIT = f"The run reached the limit of {MAX_RUN_TOOLS} source tool calls."
BUDGET_MESSAGE = "No call budget or time remains for this request. Finish from existing evidence and state the gap."
DEFAULT_HINT = "Adjust the arguments or use another source tool, or record the gap."
RECOVERY_HINTS = {
    "invalid_arguments": "Correct the arguments against the tool schema and retry once.",
    "invalid_path": "Use a repository-relative path from search_code or list_files.",
    "file_not_found": "Find the path with list_files or search_code before reading.",
    "document_not_found": "Use page_id and version from list_documents or search_documents.",
    "section_not_found": "Search the page for a section_id or read the whole page.",
    "ambiguous_section": "Search the page and read by section_id.",
    "source_too_large": "Read a narrower line range or search for the relevant lines.",
    "unsupported_encoding": "The source is not UTF-8 text. Use another source and record the gap.",
    "timeout": "Narrow the query or path scope before retrying.",
    "source_timeout": "Narrow the query or path scope before retrying.",
    "repository_unavailable": "This repository cannot be read in this run. Use other sources and record the gap.",
    "repository_not_found": "Use a repo_id from the snapshot.",
    "git_read_failed": "Check the path and revision from the snapshot, then use other sources if it persists.",
    "knowledge_unavailable": "The Confluence snapshot is unavailable. Record the gap.",
    "graph_result_not_found": "Use a result_id returned by codegraph_explore in this run.",
}
T = TypeVar("T")


def token_usage(usage: dict) -> dict[str, int]:
    """Normalise Chat Completions and Responses usage fields when the gateway reports them."""
    fields = {
        "input": ("prompt_tokens", "input_tokens"),
        "output": ("completion_tokens", "output_tokens"),
        "total": ("total_tokens",),
    }
    counts = {}
    for name, keys in fields.items():
        value = next((usage[key] for key in keys if isinstance(usage.get(key), int)), None)
        if value is not None:
            counts[name] = value
    return counts


class RunDeadlineExceeded(TimeoutError):
    pass


def load_system_prompt(*names: str) -> str:
    return "\n\n".join(
        (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
        for filename in ("common.md", "glossary.md", *names)
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
        self, settings: Settings, sources: "Sources", snapshot: dict, emit: Callable[[dict], None], *, deadline: float,
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
        # Set when the model could not deliver the run's final output, as opposed to a bounded gap.
        self.failure: str | None = None
        self._limitation_count = 0
        self._search_history: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=MAX_SEARCH_HISTORY))
        self._retrieval_coverage: dict[str, list[dict]] = defaultdict(list)
        self._tool_errors: dict[str, list[dict]] = defaultdict(list)
        self.tool_count = 0
        self.span_count = 0
        self._span_tasks: dict[str, str] = {}
        self.llm_calls = 0
        self.llm_seconds = 0.0
        self.token_usage: dict[str, int] = {}

    def usage_summary(self) -> str:
        tokens = ", ".join(f"{key}={value}" for key, value in self.token_usage.items()) or "not reported"
        return (
            f"LLM calls={self.llm_calls} ({self.llm_seconds:.2f} s), source tools={self.tool_count}, "
            f"evidence={len(self.evidence)}, tokens {tokens}"
        )

    def has_tool_budget(self) -> bool:
        return self.tool_count < MAX_RUN_TOOLS and self.remaining() > 0

    def note(self, text: str):
        self._limitation_count += 1
        if text not in self.limits:
            self.limits.append(text)

    def remaining(self) -> float:
        return self.deadline - monotonic()

    def new_span(self, prefix: str) -> str:
        self.span_count += 1
        return f"{prefix}-{self.span_count}-{uuid4().hex[:6]}"

    def redact_text(self, value: str, *, preserve_length: bool = False) -> str:
        def mask(secret: str) -> str:
            return "*" * len(secret) if preserve_length else "[redacted]"

        key = self.settings.llm.api_key
        if key:
            value = value.replace(key, mask(key))
        value = re.sub(
            r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]+)", lambda match: match[1] + mask(match[2]), value,
        )
        value = re.sub(
            r"(?i)\b((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)([^\s,;]+)",
            lambda match: match[1] + mask(match[2]), value,
        )
        return value

    def safe_text(self, value: str, limit: int) -> str:
        return self.redact_text(value)[:limit]

    def source_window_for_model(self, result: dict) -> dict:
        # Apply the same redactions to the returned and recorded source window.
        if "content" in result:
            original = result["content"]
            visible = self.redact_text(original, preserve_length=True)
            return {**result, "content": visible, "redacted": True} if visible != original else result
        if "lines" in result:
            original = result["lines"]
            visible = [{**line, "text": self.redact_text(line["text"], preserve_length=True)} for line in original]
            return {**result, "lines": visible, "redacted": True} if visible != original else result
        return result

    def trace_input(self, arguments: dict, fields: tuple[str, ...], *, list_limit: int = 8) -> dict:
        summary = {}
        for key in fields:
            value = arguments.get(key)
            if isinstance(value, str):
                summary[key] = self.safe_text(value, 180)
            elif isinstance(value, int):
                summary[key] = value
            elif isinstance(value, list):
                summary[key] = [self.safe_text(item, 80) for item in value[:list_limit] if isinstance(item, str)]
        return summary

    @contextmanager
    def agent_span(self, agent: str, stage: str, parent: str, task: dict | None = None):
        span = self.new_span(agent)
        start = monotonic()
        meta = {"agent_id": agent, "stage": stage, "span_id": span, "parent_span_id": parent}
        if task:
            meta["input_summary"] = self.trace_input(task, ("question", "task_id"))
        previous_limits = self._limitation_count
        self.emit({**meta, "type": "agent.started", "status": "running"})
        if task and task.get("task_id"):
            self._span_tasks[span] = task["task_id"]
        status = "succeeded"
        try:
            yield span
        except Exception:
            status = "failed"
            raise
        finally:
            self._span_tasks.pop(span, None)
            self._retrieval_coverage.pop(span, None)
            self._tool_errors.pop(span, None)
            if status == "succeeded" and self._limitation_count > previous_limits:
                status = "partial"
            self.emit({**meta, "type": "agent.finished", "status": status, "elapsed_ms": int((monotonic() - start) * 1000)})

    def record_evidence(self, evidence: dict, span: str, agent_span: str) -> str:
        evidence_id = f"E{len(self.evidence) + 1}"
        task_id = self._span_tasks.get(agent_span)
        self.evidence[evidence_id] = {
            **evidence,
            "evidence_id": evidence_id,
            "tool_span_id": span,
            **({"task_id": task_id} if task_id is not None else {}),
        }
        return evidence_id

    def call_tool(
        self, agent: str, parent: str, tools: dict[str, ToolDefinition], name: str, arguments: dict, *, delegation: bool = False,
    ) -> dict:
        if not self.has_tool_budget():
            self.note(TIME_LIMIT if self.remaining() <= 0 else TOOL_LIMIT)
            return {"error": "budget_exhausted", "message": BUDGET_MESSAGE}
        definition = tools.get(name)
        if definition is None:
            self.note("The model requested a tool outside this investigator's toolset.")
            return {"error": "unknown_tool", "message": "Use one of the tools offered in this request.", "available_tools": sorted(tools)}
        if delegation:
            try:
                return definition.tool.invoke(arguments)
            except (TypeError, ValueError, ValidationError):
                self.note("The investigation assignment had invalid arguments.")
                return {
                    "error": "invalid_arguments",
                    "message": "Send a non-empty question and, when a plan exists, a task_id from that plan.",
                }
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
        graph_call = name in GRAPH_TOOLS
        try:
            result = run_before_deadline(self.deadline, definition.tool.invoke, arguments)
            if definition.evidence_factory:
                result = self.source_window_for_model(result)
                if evidence := definition.evidence_factory(result):
                    if result.get("redacted"):
                        evidence["redacted"] = True
                    evidence_id = self.record_evidence(evidence, span, parent)
                    result = {**result, "evidence_ids": [evidence_id]}
                    output["evidence_ids"] = [evidence_id]
            for key in ("results", "lines"):
                if isinstance(result.get(key), list):
                    output["count"] = len(result[key])
            for key in (
                "section_id", "status", "total", "returned", "has_more", "truncated", "line_truncated",
                "start_line", "end_line", "next_offset", "next_start_line", "next_column_offset", "redacted",
            ):
                if key in result:
                    value = result[key]
                    output[key] = self.safe_text(value, 180) if isinstance(value, str) else value
            if graph_call:
                for key in ("captured_count", "result_format", "repo_id", "provider"):
                    if key in result:
                        value = result[key]
                        output[key] = self.safe_text(value, 180) if isinstance(value, str) else value
                if result_id := result.get("result_id"):
                    output["result_ref"] = sha256(result_id.encode("ascii")).hexdigest()[:16]
            if "effective_scope" in result:
                output["effective_scope"] = self.trace_input(
                    result["effective_scope"], definition.trace_fields, list_limit=16,
                )
            if result.get("unavailable"):
                output["unavailable_count"] = len(result["unavailable"])
            if warnings := result.get("warnings"):
                if graph_call:
                    output["warning_count"] = len(warnings)
                else:
                    output["warnings"] = [self.safe_text(warning, 240) for warning in warnings[:3]]
            if result.get("status") in {"partial", "unavailable"}:
                status = "partial" if result["status"] == "partial" else "failed"
                detail = (
                    f"CodeGraph coverage is {result['status']}."
                    if graph_call else " ".join(output.get("warnings", [])) or "Some requested sources are unavailable."
                )
                self.note(f"{name} · {detail}")
            return result
        except SourceError as exc:
            error_code = self.safe_text(str(exc.code), 80)
            error_summary = self.safe_text(str(exc), 240)
            status = "timeout" if "timeout" in error_code.lower() else "failed"
        except TimeoutError as exc:
            if isinstance(exc, RunDeadlineExceeded):
                self.note(TIME_LIMIT)
            error_code, error_summary, status = "timeout", "The tool call timed out.", "timeout"
        except ValidationError as exc:
            details = ". ".join(
                f"{'.'.join(map(str, problem['loc']))}: {problem['msg']}"
                for problem in exc.errors(include_input=False, include_context=False, include_url=False)[:3]
            )
            error_code, error_summary, status = "invalid_arguments", self.safe_text(details, 240), "failed"
        except (TypeError, ValueError):
            error_code, error_summary, status = "invalid_arguments", "The tool received invalid arguments.", "failed"
        except Exception as exc:
            log.warning("Tool %s failed (%s)", name, type(exc).__name__, exc_info=True)
            error_code, error_summary, status = "tool_failed", f"The tool call failed ({type(exc).__name__}).", "failed"
        finally:
            if name in SEARCH_TOOLS:
                scope = output.get("effective_scope", meta["input_summary"])
                self._search_history[agent].append({
                    "tool": name, "query": meta["input_summary"].get("query"),
                    "scope": {key: value for key, value in scope.items() if key != "query"},
                    "returned": output.get("returned", 0), "status": output.get("status", status),
                    "has_more": output.get("has_more"),
                })
            if graph_call or name in SEARCH_TOOLS:
                coverage = {
                    key: output[key] for key in (
                        "repo_id", "effective_scope", "returned", "captured_count", "has_more",
                        "truncated", "result_format", "provider", "warning_count", "unavailable_count",
                    ) if key in output
                }
                if graph_call and "repo_id" in meta["input_summary"]:
                    coverage["repo_id"] = meta["input_summary"]["repo_id"]
                self._retrieval_coverage[parent].append({
                    **coverage, "tool": name, "status": output.get("status", status),
                    **({"error": error_code} if error_code else {}),
                })
            self.emit({
                **meta, "type": "tool.finished", "status": status,
                "elapsed_ms": int((monotonic() - start) * 1000), "output_summary": output,
                **({"error_code": error_code, "error_summary": error_summary} if error_code else {}),
            })
        self.note(f"{name} · {error_summary}")
        failure = {
            "error": error_code, "message": error_summary, "tool": name, "status": status,
            "input": meta["input_summary"], "hint": RECOVERY_HINTS.get(error_code, DEFAULT_HINT),
        }
        self._tool_errors[parent].append({key: failure[key] for key in ("tool", "error", "message", "status", "input")})
        return failure

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, agent: str = "main") -> dict:
        self.llm_calls += 1
        call = self.llm_calls
        start = monotonic()
        try:
            result = run_before_deadline(self.deadline, self.client.chat, messages, tools, timeout=self.remaining())
        except RunDeadlineExceeded:
            self.llm_seconds += monotonic() - start
            log.warning("LLM call %d (%s) stopped at the run time limit after %.2f s", call, agent, monotonic() - start)
            self.note(TIME_LIMIT)
            raise LLMError(TIME_LIMIT) from None
        except LLMError as exc:
            self.llm_seconds += monotonic() - start
            log.warning("LLM call %d (%s) failed after %.2f s: %s", call, agent, monotonic() - start, exc)
            raise
        elapsed = monotonic() - start
        self.llm_seconds += elapsed
        usage = token_usage(result.usage)
        for key, value in usage.items():
            self.token_usage[key] = self.token_usage.get(key, 0) + value
        log.info(
            "LLM call %d (%s) %.2f s, %d messages, %d tools, %d tool calls, tokens %s", call, agent, elapsed,
            len(messages), len(tools or []), len(result.message.get("tool_calls") or []),
            ", ".join(f"{key}={value}" for key, value in usage.items()) or "not reported",
        )
        return result.message

    def tool_message(
        self, call: dict, invoke: Callable[[str, dict], dict] | None = None, *, note_invalid: bool = True,
    ) -> dict:
        if invoke is None:
            result = {"error": "budget_exhausted", "message": BUDGET_MESSAGE}
        else:
            try:
                arguments = json_object(call["function"]["arguments"])
            except LLMError:
                if note_invalid:
                    self.note("The model returned tool arguments that could not be parsed.")
                result = {"error": "invalid_arguments", "message": "Send the tool arguments as one JSON object."}
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
            message = self.chat(messages, active_tools, agent=agent)
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
        if history := self._search_history.get(agent):
            data = {**data, "search_history": list(history)}
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": as_json(data)},
        ]
        try:
            findings = json_object(self.chat_with_tools(agent, span, messages, tools))
        except LLMError as exc:
            self.note(f"{stage} · {exc}")
            findings = {"open_questions": [f"Investigation incomplete. See the trace for {stage.lower()}."]}
        findings.pop("retrieval_coverage", None)
        findings.pop("tool_errors", None)
        if coverage := self._retrieval_coverage.pop(span, []):
            findings["retrieval_coverage"] = coverage
        if errors := self._tool_errors.pop(span, []):
            # Main sees which source reads failed and why, so it can retry, redirect or record the gap.
            findings["tool_errors"] = errors
        return findings
