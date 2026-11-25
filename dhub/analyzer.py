"""Build and execute the LangGraph for one impact-analysis run."""

import importlib

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from .agent_runtime import AgentRuntime, State
from .agents.code import CodeInvestigator
from .agents.domain import DomainKnowledgeInvestigator
from .agents.main import DEMO_REQUIREMENT as DEMO_REQUIREMENT, MainProcessImpactAnalyser
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


def _run_workflow(runtime: AgentRuntime, requirement: str) -> dict:
    main = MainProcessImpactAnalyser(runtime)
    domain = DomainKnowledgeInvestigator(runtime)
    code = CodeInvestigator(runtime)
    investigators = {domain.agent_id: domain, code.agent_id: code}
    with runtime.agent_span(main.agent_id, "理解需求、派单和综合", "run") as span:
        def follow_up(state: State) -> dict:
            task = state["follow_up"]
            agent = task["agent"]
            finding = investigators[agent].run(state, span, task)
            return {agent: [*state.get(agent, []), finding], "follow_up": None, "followed_up": True}

        graph = StateGraph(State)
        graph.add_node("plan_requirement", main.plan)
        graph.add_node("investigate_domain", lambda state: {"domain": [domain.run(state, span)]})
        graph.add_node("investigate_code", lambda state: {"code": [code.run(state, span)]})
        graph.add_node("synthesize", main.synthesize)
        graph.add_node("follow_up_investigation", follow_up)
        graph.add_edge(START, "plan_requirement")
        graph.add_edge("plan_requirement", "investigate_domain")
        graph.add_edge("investigate_domain", "investigate_code")
        graph.add_edge("investigate_code", "synthesize")
        graph.add_conditional_edges("synthesize", lambda state: "follow_up_investigation" if state.get("follow_up") else END)
        graph.add_edge("follow_up_investigation", "synthesize")
        return graph.compile().invoke({"requirement": requirement})["report"]


def analyze(settings: Settings, requirement: str, emit, set_snapshot) -> tuple[str, dict | None, str | None]:
    try:
        sources = Sources(
            data_mode=settings.data_mode,
            manifest_path=settings.manifest_path,
            repositories=settings.repositories,
            codegraph=load_codegraph(settings.codegraph_adapter) if settings.data_mode == "project" else None,
        )
        snapshot = sources.snapshot()
        set_snapshot(snapshot)
        runtime = AgentRuntime(settings, sources, snapshot, emit)
        knowledge = snapshot.get("knowledge", {})
        if knowledge.get("status") != "available":
            runtime.note("Confluence 知识快照不可用。")
        repositories = snapshot.get("repositories", [])
        if len(repositories) < 2 or any(repo.get("status") != "available" for repo in repositories):
            runtime.note("至少有一个代码仓库的快照不可用。")
        if settings.data_mode == "project" and any(
            repo.get("status") == "available" and repo.get("codegraph_commit_sha") != repo.get("commit_sha")
            for repo in repositories
        ):
            runtime.note("部分仓库缺少匹配的 CodeGraph 索引，调用关系以可读取的源码为依据。")
        # The app stores its own trace. Keep inherited LangSmith settings out of the run.
        with tracing_context(enabled=False):
            report = _run_workflow(runtime, requirement)
        return ("partial" if runtime.limits else "succeeded"), report, None
    except LLMError as exc:
        return "failed", None, str(exc)
