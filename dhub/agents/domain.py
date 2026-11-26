"""Domain agent for business rules, process steps and Confluence evidence."""

from ..agent_runtime import AgentRuntime, State, load_system_prompt

class DomainKnowledgeInvestigator:
    agent_id = "domain"

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime
        self.tools = runtime.sources.domain.toolset()

    def run(self, state: State, parent_span: str, task: dict | None = None) -> dict:
        stage = "业务补查" if task else "调查业务规则"
        with self.runtime.agent_span(self.agent_id, stage, parent_span) as span:
            if self.runtime.offline:
                return self.run_offline(state, span)
            return self.runtime.investigate(
                self.agent_id, span, stage, load_system_prompt("domain.md"), self.tools,
                {
                    "requirement": state["requirement"], "plan": state["plan"], "task": task,
                    "domain_findings": [], "snapshot": self.runtime.snapshot,
                },
            )

    def run_offline(self, state: State, span: str) -> dict:
        def tool(name, **kwargs):
            return self.runtime.call_tool(self.agent_id, span, self.tools, name, kwargs)

        if not state["plan"]["demo_case"]:
            tool("search_documents", query=state["requirement"][:160])
            return {"coverage": "离线模式执行了源查询，固定演示场景以外的结论需配置模型。"}
        tool("list_documents", limit=8)
        tool("search_documents", query="mandatory campaign frequency cap")
        tool("read_document", page_id="DEMO-FD2", version="1", section="Phase 5 / Step 18")
        tool("read_document", page_id="DEMO-SETTINGS", version="1", section="Frequency Cap")
        return {"code_hints": ["should_allow_campaign", "allow_mandate_campaign_override"]}
