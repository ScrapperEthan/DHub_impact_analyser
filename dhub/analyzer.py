"""Run the DHub agents for impact analysis and conversational questions."""

import logging
from time import monotonic

from langsmith import tracing_context

from .agents.main import DEMO_REQUIREMENT as DEMO_REQUIREMENT, MainProcessImpactAnalyser
from .agents.runtime import TIME_LIMIT, AgentRuntime, RunDeadlineExceeded, run_before_deadline, run_deadline
from .config import Settings
from .llm import LLMError
from .sources.codegraph import UnavailableCodeGraphAdapter, load_codegraph
from .sources.manager import Sources
from .sources.utils import SourceError

log = logging.getLogger(__name__)
MAX_CHAT_EXCERPT_CHARS = 2_000


def _prepare_sources(settings: Settings, deadline: float) -> tuple[Sources, dict]:
    codegraph = None
    if settings.data_mode == "project":
        start = monotonic()
        try:
            codegraph = load_codegraph(settings.codegraph_adapter)
        except SourceError as error:
            log.warning("CodeGraph adapter unavailable: %s (%s)", error, error.code, exc_info=True)
            codegraph = UnavailableCodeGraphAdapter(error)
        else:
            if codegraph is not None:
                log.info("CodeGraph adapter %s loaded in %.2f s", settings.codegraph_adapter, monotonic() - start)
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
    start = monotonic()
    try:
        sources, snapshot = run_before_deadline(deadline, _prepare_sources, settings, deadline)
    except RunDeadlineExceeded:
        log.warning("Source preparation reached the run time limit after %.2f s", monotonic() - start)
        raise LLMError(TIME_LIMIT) from None
    log.info("Sources prepared in %.2f s", monotonic() - start)
    set_snapshot(snapshot)
    return AgentRuntime(settings, sources, snapshot, emit, deadline=deadline)


def _chat_evidence(evidence: dict[str, dict]) -> list[dict]:
    # Let the chat page open cited evidence. Long excerpts are shortened for the browser session.
    return [
        {**entry, "excerpt": entry["excerpt"][:MAX_CHAT_EXCERPT_CHARS],
         **({"excerpt_truncated": True} if len(entry["excerpt"]) > MAX_CHAT_EXCERPT_CHARS else {})}
        for entry in evidence.values()
    ]


def answer_chat(settings: Settings, messages: list[dict], emit=None) -> str:
    start = monotonic()
    runtime = None
    outcome = "failed"
    emit = emit or (lambda event: None)
    try:
        with tracing_context(enabled=False):
            runtime = _start_runtime(settings, emit, lambda snapshot: None)
            reply = MainProcessImpactAnalyser(runtime).reply(messages)
        if runtime.evidence:
            emit({"type": "chat.evidence", "evidence": _chat_evidence(runtime.evidence)})
        outcome = "partial" if runtime.limits else "succeeded"
        return reply
    finally:
        log.info("Chat %s in %.2f s · %s", outcome, monotonic() - start, runtime.usage_summary() if runtime else "no runtime")


def analyze(settings: Settings, requirement: str, emit, set_snapshot) -> tuple[str, dict | None, str | None]:
    start = monotonic()
    runtime = None
    status, report, detail = "failed", None, None
    try:
        # The app stores its own trace. Keep inherited LangSmith settings out of the run.
        with tracing_context(enabled=False):
            runtime = _start_runtime(settings, emit, set_snapshot)
            snapshot = runtime.snapshot
            if snapshot["knowledge"].get("status") == "unavailable":
                runtime.note("The Confluence snapshot is unavailable. " + snapshot["knowledge"].get("error", ""))
            elif snapshot["knowledge"].get("status") == "partial":
                runtime.note("Some pages in the Confluence snapshot are unavailable. See skipped_pages in the source snapshot.")
            repositories = snapshot["repositories"]
            if not repositories:
                runtime.note("No repository snapshot is configured.")
            elif any(repo.get("status") != "available" for repo in repositories):
                runtime.note("At least one repository snapshot is unavailable.")
            missing_graph = [
                f"{repo['repo_id']} ({repo.get('codegraph_reason')})" for repo in repositories
                if repo.get("status") == "available" and repo.get("codegraph_status") != "available"
            ]
            if settings.data_mode == "project" and missing_graph:
                runtime.note(
                    "Some repositories lack a matching CodeGraph index: " + ", ".join(missing_graph)
                    + ". Call relationships are based on available source code."
                )
            report = MainProcessImpactAnalyser(runtime).analyze(requirement)
        if runtime.failure:
            # The model could not deliver a report. Keep the evidence and give the cause.
            status, detail = "failed", runtime.failure
        else:
            status = "partial" if runtime.limits else "succeeded"
        return status, report, detail
    except LLMError as exc:
        detail = str(exc)
        return "failed", None, detail
    finally:
        log.info(
            "Analysis %s in %.2f s%s · %s", status, monotonic() - start, f" ({detail})" if detail else "",
            runtime.usage_summary() if runtime else "no runtime",
        )
