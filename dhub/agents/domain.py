"""Domain agent for business rules, process steps and Confluence evidence."""

from .runtime import AgentRuntime, State, load_system_prompt

class DomainKnowledgeInvestigator:
    agent_id = "domain"

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime
        self.tools = runtime.sources.domain.toolset()

    def run(self, state: State, parent_span: str, task: dict | None = None) -> dict:
        stage = "Business rule follow-up" if state.get("domain") else "Check business rules"
        with self.runtime.agent_span(self.agent_id, stage, parent_span, task) as span:
            if self.runtime.offline:
                return self.run_offline(state, span)
            return self.runtime.investigate(
                self.agent_id, span, stage, load_system_prompt("domain.md"), self.tools,
                {
                    "requirement": state["requirement"], "plan": state["plan"], "task": task,
                    "domain_findings": state.get("domain", []), "code_findings": state.get("code", []),
                    "snapshot": self.runtime.snapshot,
                },
            )

    def run_offline(self, state: State, span: str) -> dict:
        def tool(name, **kwargs):
            return self.runtime.call_tool(self.agent_id, span, self.tools, name, kwargs)

        if not state["demo_case"]:
            tool("search_documents", query=state["requirement"][:160])
            return {"coverage": "Searched the demo documents. A model gateway is required to analyze other requests."}
        tool("list_documents", limit=8)
        tool("search_documents", query="mandatory campaign frequency cap")
        rule = tool("read_document", page_id="DEMO-FD2", version="1", section="Phase 5 / Step 18")
        settings = tool("read_document", page_id="DEMO-SETTINGS", version="1", section="Frequency Cap")
        return {"code_hints": {
            "business_terms": ["Frequency Cap", "Mandatory Campaign"],
            "possible_identifiers": ["should_allow_campaign"],
            "config_keys": ["allow_mandate_campaign_override"],
            "suspected_modules": [],
            "evidence_ids": rule.get("evidence_ids", []) + settings.get("evidence_ids", []),
        }}
