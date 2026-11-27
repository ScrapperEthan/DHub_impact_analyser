"""Run the DHub agents for impact analysis and conversational questions."""

import importlib

from langsmith import tracing_context

from .agent_runtime import TIME_LIMIT, AgentRuntime, RunDeadlineExceeded, run_before_deadline, run_deadline
from .agents.main import DEMO_REQUIREMENT as DEMO_REQUIREMENT, MainProcessImpactAnalyser
from .codegraph import UnavailableCodeGraphAdapter
from .config import Settings
from .llm import LLMError
from .sources import Sources


def load_codegraph(spec: str):
    if not spec:
        return None
    module, separator, factory = spec.partition(":")
    if not separator or not module or not factory:
        raise ValueError("DHUB_CODEGRAPH_ADAPTER must use module:factory")
    return getattr(importlib.import_module(module), factory)()


def _prepare_sources(settings: Settings, deadline: float) -> tuple[Sources, dict]:
    codegraph = None
    if settings.data_mode == "project":
        try:
            codegraph = load_codegraph(settings.codegraph_adapter)
        except Exception:
            codegraph = UnavailableCodeGraphAdapter()
    sources = Sources(
        data_mode=settings.data_mode,
        manifest_path=settings.manifest_path,
        repositories=settings.repositories,
        codegraph=codegraph,
        deadline=deadline,
    )
    return sources, sources.snapshot()


def _start_runtime(settings: Settings, emit, set_snapshot) -> AgentRuntime:
    deadline = run_deadline(settings)
    try:
        sources, snapshot = run_before_deadline(deadline, _prepare_sources, settings, deadline)
    except RunDeadlineExceeded:
        raise LLMError(TIME_LIMIT) from None
    set_snapshot(snapshot)
    return AgentRuntime(settings, sources, snapshot, emit, deadline=deadline)


def answer_chat(settings: Settings, messages: list[dict], emit=None) -> str:
    with tracing_context(enabled=False):
        runtime = _start_runtime(settings, emit or (lambda event: None), lambda snapshot: None)
        return MainProcessImpactAnalyser(runtime).reply(messages)


def analyze(settings: Settings, requirement: str, emit, set_snapshot) -> tuple[str, dict | None, str | None]:
    try:
        # The app stores its own trace. Keep inherited LangSmith settings out of the run.
        with tracing_context(enabled=False):
            runtime = _start_runtime(settings, emit, set_snapshot)
            snapshot = runtime.snapshot
            if snapshot["knowledge"].get("status") == "unavailable":
                runtime.note("The Confluence snapshot is unavailable.")
            elif snapshot["knowledge"].get("status") == "partial":
                runtime.note("Some pages in the Confluence snapshot are unavailable.")
            repositories = snapshot["repositories"]
            if not repositories:
                runtime.note("No repository snapshot is configured.")
            elif any(repo.get("status") != "available" for repo in repositories):
                runtime.note("At least one repository snapshot is unavailable.")
            if settings.data_mode == "project" and any(
                repo.get("status") == "available" and repo.get("codegraph_status") != "available"
                for repo in repositories
            ):
                runtime.note("Some repositories lack a matching CodeGraph index. Call relationships are based on available source code.")
            report = MainProcessImpactAnalyser(runtime).analyze(requirement)
        return ("partial" if runtime.limits else "succeeded"), report, None
    except LLMError as exc:
        return "failed", None, str(exc)
