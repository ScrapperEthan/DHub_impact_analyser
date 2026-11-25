"""Code agent for pinned repositories, dependencies, configuration and tests."""

from ..agent_runtime import AgentRuntime, State, load_system_prompt

CODE_TOOLS = {
    "get_repository_metadata": "Get actual pinned branch, commit and CodeGraph coverage for a repository.",
    "search_code": "Find literal terms across configured repositories. A match is a discovery hint.",
    "read_file": "Read numbered original source lines for a pinned repository. Use its evidence ID.",
    "codegraph_explore": "Explore static relations for a symbol in an available CodeGraph index.",
    "codegraph_read_result": "Read the next window of the saved CodeGraph result.",
}


class CodeInvestigator:
    agent_id = "code"

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime
        self.tools = runtime.bind_tools(CODE_TOOLS)

    def run(self, state: State, parent_span: str, task: dict | None = None) -> dict:
        stage = "代码补查" if task else "调查代码实现"
        with self.runtime.agent_span(self.agent_id, stage, parent_span) as span:
            if self.runtime.offline:
                return self.run_offline(state, span, bool(task))
            return self.runtime.investigate(
                self.agent_id, span, stage, load_system_prompt("code.md"), self.tools,
                {
                    "requirement": state["requirement"], "plan": state["plan"], "task": task,
                    "domain_findings": state.get("domain", []), "snapshot": self.runtime.snapshot,
                },
            )

    def run_offline(self, state: State, span: str, follow_up: bool) -> dict:
        def tool(name, **kwargs):
            return self.runtime.call_tool(self.agent_id, span, self.tools, name, kwargs)

        if not state["plan"]["demo_case"]:
            tool("search_code", query=state["requirement"][:160])
            return {"coverage": "离线模式执行了源查询，固定演示场景以外的结论需配置模型。"}
        if follow_up:
            prior = state["code"][0].get("graph", {})
            if prior.get("next_offset") is not None and prior.get("result_id"):
                tool("codegraph_read_result", result_id=prior["result_id"], offset=prior["next_offset"], limit=4)
            tool("read_file", repo_id="fixture-decision", path="config/global_settings.json")
            tool("read_file", repo_id="fixture-decision", path="tests/test_frequency.py")
            tool("read_file", repo_id="fixture-channel", path="channel/delivery.py")
            return {"coverage": "补查配置、已有测试及下游消费点"}
        for repo_id in ("fixture-decision", "fixture-channel"):
            tool("get_repository_metadata", repo_id=repo_id)
        tool("search_code", query="should_allow_campaign")
        graph = tool("codegraph_explore", repo_id="fixture-decision", query="should_allow_campaign", limit=1)
        for path in ("decision/frequency.py", "decision/inbound.py", "decision/outbound.py"):
            tool("read_file", repo_id="fixture-decision", path=path)
        return {"graph": graph}
