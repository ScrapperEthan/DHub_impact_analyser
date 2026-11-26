"""Main agent for impact analysis and conversational answers."""

from collections.abc import Callable
from typing import TypedDict, TypeVar

from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from ..agent_runtime import (
    MAX_DELEGATIONS, MAX_RUN_TOOLS, TIME_LIMIT, TOOL_LIMIT, AgentRuntime, State, as_json, json_object,
    load_system_prompt,
)
from ..llm import LLMError
from ..planning import OPEN_TASK_STATUSES, InvestigationPlan, PlanTask, TaskId
from ..report import Report, ground_report
from ..tools.registry import build_toolset, define_tool
from .code import CodeInvestigator
from .domain import DomainKnowledgeInvestigator

DEMO_REQUIREMENT = (
    "Mandatory outbound campaigns should no longer bypass the frequency cap, "
    "even when Allow mandate campaign override is enabled."
)
REPORT_SCHEMA = as_json(Report.model_json_schema())
MAX_REPLY_CHARS = 12_000
MAX_PLAN_UPDATES = 50
DELEGATION_LIMIT = f"The run reached the limit of {MAX_DELEGATIONS} investigator assignments."
FREQUENCY_TERMS = ("frequency cap", "频控", "触达频次", "频次上限")
Result = TypeVar("Result", str, Report)


class MainTurn(TypedDict, total=False):
    messages: list[dict]
    output: str | Report
    repairing: bool
    reviewing_plan: bool


class MainProcessImpactAnalyser:
    agent_id = "main"

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    def reply(self, messages: list[dict]) -> str:
        with self.runtime.agent_span(self.agent_id, "Chat", "chat") as span:
            if self.runtime.offline:
                return self._offline_reply(messages, span)
            return self._run_online(
                messages[-1]["content"], messages, span,
                load_system_prompt("main-plan.md", "main-chat.md"), self._parse_reply,
            )

    def analyze(self, requirement: str) -> dict:
        with self.runtime.agent_span(self.agent_id, "Analyze change request", "run") as span:
            if self.runtime.offline:
                report = self._offline_analysis(requirement, span)
            else:
                prompt = load_system_prompt("main-plan.md", "main-report.md") + "\n\nReport JSON Schema:\n" + REPORT_SCHEMA
                messages = [{"role": "user", "content": as_json({
                    "requirement": requirement,
                    "snapshot": self.runtime.snapshot,
                    "limitations": self.runtime.limits,
                    "source_tool_budget": MAX_RUN_TOOLS,
                })}]
                try:
                    report = self._run_online(requirement, messages, span, prompt, self._parse_report)
                except (LLMError, ValidationError):
                    self.runtime.note("The model did not produce a valid report. The available evidence and coverage are included.")
                    report = self.fallback(requirement)
            report.open_questions = list(dict.fromkeys([*report.open_questions, *self.runtime.limits]))
            return ground_report(report, self.runtime.evidence)

    def _run_online(
        self, requirement: str, messages: list[dict], span: str, prompt: str, parse: Callable[[str | None], Result],
    ) -> Result:
        domain = DomainKnowledgeInvestigator(self.runtime)
        code = CodeInvestigator(self.runtime)
        findings: State = {"requirement": requirement, "plan": None, "domain": [], "code": []}
        task_agents: dict[str, str] = {}
        remaining_delegations = MAX_DELEGATIONS
        remaining_plan_updates = MAX_PLAN_UPDATES
        # A model can return unavailable tools without consuming either tool budget.
        remaining_tool_rounds = MAX_DELEGATIONS + MAX_PLAN_UPDATES

        def investigate(
            investigator: DomainKnowledgeInvestigator | CodeInvestigator, question: str, task_id: str | None,
        ) -> dict:
            question = question.strip()
            if not question:
                raise ValueError("Investigation question is empty")
            plan = findings["plan"]
            assignment = {"agent": investigator.agent_id, "question": question}
            if task_id is not None:
                task = next((task for task in (plan or {}).get("tasks", []) if task["id"] == task_id), None)
                if task is None:
                    return {"error": "unknown_task_id", "message": "Use a task ID from the current plan.", "plan": plan}
                if task["agent"] != investigator.agent_id:
                    return {"error": "task_agent_mismatch", "message": "Use the investigator assigned to this task.", "plan": plan}
                assignment["task_id"] = task_id
            elif plan is not None:
                return {"error": "task_id_required", "message": "Include the task ID from the current plan.", "plan": plan}

            evidence_start = len(self.runtime.evidence)
            try:
                finding = investigator.run(findings, span, assignment)
            finally:
                if plan is not None:
                    plan = self._publish_plan(findings, span, plan)
            findings[investigator.agent_id].append(finding)
            evidence = list(self.runtime.evidence.values())[evidence_start:]
            return {
                "findings": finding,
                "evidence": [{**entry, "excerpt": entry["excerpt"][:1200]} for entry in evidence],
                "limitations": self.runtime.limits,
                **({"task_id": task_id, "plan": plan} if plan is not None else {}),
            }

        def investigate_domain(question: str, task_id: TaskId | None = None) -> dict:
            """Check DHub business rules, steps and exceptions. Include task_id when an explicit plan exists."""
            return investigate(domain, question, task_id)

        def investigate_code(question: str, task_id: TaskId | None = None) -> dict:
            """Check DHub code, configuration and tests. Include task_id when an explicit plan exists."""
            return investigate(code, question, task_id)

        def update_plan(
            goal: str, tasks: list[PlanTask], reason: str,
            scope: dict[str, str] | None = None, candidate_steps: list[str] | None = None,
        ) -> dict:
            """Create or replace the full public investigation plan. Return the current version and tasks."""
            for task in tasks:
                if self.runtime.safe_text(task.id, 32) != task.id or task_agents.get(task.id, task.agent) != task.agent:
                    return {
                        "error": "invalid_task_id", "plan": findings["plan"],
                        "message": "Use a public, stable task ID. Use a new ID if the assigned investigator changes.",
                    }
            plan = {
                "goal": goal, "scope": scope or {}, "candidate_steps": candidate_steps or [],
                "tasks": [task.model_dump(exclude_none=True) for task in tasks], "reason": reason,
            }
            snapshot = self._publish_plan(findings, span, plan)
            task_agents.update({task.id: task.agent for task in tasks})
            return {"plan": snapshot}

        investigators = build_toolset(define_tool(investigate_domain), define_tool(investigate_code))
        investigation_schema = [convert_to_openai_tool(definition.tool) for definition in investigators.values()]
        plan_tool = define_tool(update_plan, args_schema=InvestigationPlan).tool
        plan_schema = [convert_to_openai_tool(plan_tool)]

        def can_delegate() -> bool:
            return remaining_delegations > 0 and self.runtime.has_tool_budget()

        def can_plan() -> bool:
            return remaining_plan_updates > 0 and self.runtime.remaining() > 0

        def ask_main(turn: MainTurn) -> dict:
            nonlocal remaining_tool_rounds
            transcript = turn["messages"]
            repairing = turn.get("repairing", False)
            active_tools = []
            if not repairing and remaining_tool_rounds > 0:
                if can_delegate():
                    active_tools.extend(investigation_schema)
                if can_plan():
                    active_tools.extend(plan_schema)
            request = transcript
            if not repairing and (not can_delegate() or not active_tools):
                request = [*transcript, {
                    "role": "system",
                    "content": "Investigation budget has been reached. Finish from existing evidence and state open gaps. "
                    "If update_plan is available, record any remaining blockers before the final response.",
                }]
            message = self.runtime.chat(request, active_tools or None)
            if active_tools and message.get("tool_calls"):
                remaining_tool_rounds -= 1
                return {"messages": [*transcript, message]}
            try:
                output = parse(message.get("content"))
            except (LLMError, ValidationError):
                if repairing:
                    raise
                return {"repairing": True, "messages": [*transcript,
                    {"role": "assistant", "content": message.get("content") or ""},
                    {"role": "system", "content": "Return a complete final response in the requested format. Use the evidence already collected."},
                ]}
            open_tasks = [
                task["id"] for task in (findings["plan"] or {}).get("tasks", [])
                if task["status"] in OPEN_TASK_STATUSES
            ]
            if open_tasks and active_tools and can_plan() and not turn.get("reviewing_plan"):
                return {"reviewing_plan": True, "messages": [*transcript, {
                    "role": "system",
                    "content": "Before the final response, resolve the open plan tasks from evidence or mark the "
                    "remaining tasks as blocked or skipped in update_plan with a public reason. Open task IDs: "
                    + as_json(open_tasks),
                }]}
            return {"output": output}

        def delegate(turn: MainTurn) -> dict:
            nonlocal remaining_delegations, remaining_plan_updates
            transcript = turn["messages"]
            results = []

            def invoke_investigator(name: str, arguments: dict) -> dict:
                return self.runtime.call_tool(self.agent_id, span, investigators, name, arguments, delegation=True)

            def invoke_plan(_name: str, arguments: dict) -> dict:
                try:
                    return plan_tool.invoke(arguments)
                except ValidationError as exc:
                    issues = [
                        {"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
                        for error in exc.errors(include_input=False, include_context=False, include_url=False)[:5]
                    ]
                    return {"error": "invalid_arguments", "issues": issues, "plan": findings["plan"]}

            for call in transcript[-1]["tool_calls"]:
                if call["function"]["name"] == "update_plan":
                    if can_plan():
                        remaining_plan_updates -= 1
                        results.append(self.runtime.tool_message(call, invoke_plan, note_invalid=False))
                    else:
                        results.append(self.runtime.tool_message(call))
                elif can_delegate():
                    remaining_delegations -= 1
                    results.append(self.runtime.tool_message(call, invoke_investigator))
                    if remaining_delegations == 0:
                        self.runtime.note(DELEGATION_LIMIT)
                else:
                    results.append(self.runtime.tool_message(call))
            return {"messages": [*transcript, *results]}

        def next_step(turn: MainTurn) -> str:
            if "output" in turn:
                return END
            return "delegate" if turn["messages"][-1].get("tool_calls") else "main"

        graph = StateGraph(MainTurn)
        graph.add_node("main", ask_main)
        graph.add_node("delegate", delegate)
        graph.add_edge(START, "main")
        graph.add_conditional_edges("main", next_step, {"delegate": "delegate", "main": "main", END: END})
        graph.add_edge("delegate", "main")
        try:
            output = graph.compile().invoke(
                {"messages": [{"role": "system", "content": prompt}, *messages]},
                {"recursion_limit": 2 * (MAX_DELEGATIONS + MAX_PLAN_UPDATES) + 6},
            )["output"]
        finally:
            reason = "The investigation ended before this task could be verified."
            if self.runtime.remaining() <= 0:
                reason = TIME_LIMIT
            elif self.runtime.tool_count >= MAX_RUN_TOOLS:
                reason = TOOL_LIMIT
            elif remaining_delegations <= 0:
                reason = DELEGATION_LIMIT
            gaps = self._finish_plan(findings, span, reason)
        if isinstance(output, str):
            missing = [gap for gap in gaps if gap not in output]
            if missing:
                suffix = "\n\nOpen Questions\n" + "\n".join(f"- {gap}" for gap in missing)
                return output[:max(0, MAX_REPLY_CHARS - len(suffix))].rstrip() + suffix[:MAX_REPLY_CHARS]
        return output

    def _publish_plan(self, state: State, span: str, plan: dict) -> dict:
        safe = self.runtime.safe_text
        evidence_by_task: dict[str, list[str]] = {}
        for evidence_id, entry in self.runtime.evidence.items():
            if "task_id" in entry:
                evidence_by_task.setdefault(entry["task_id"], []).append(evidence_id)
        public = {
            "goal": safe(plan["goal"], 400),
            "scope": {safe(key, 400): safe(value, 400) for key, value in plan["scope"].items()},
            "candidate_steps": [safe(step, 400) for step in plan["candidate_steps"]],
            "tasks": [
                {**task, "question": safe(task["question"], 400), "evidence_ids": evidence_by_task.get(task["id"], []),
                 **({"note": safe(task["note"], 400)} if "note" in task else {})}
                for task in plan["tasks"]
            ],
            "reason": safe(plan["reason"], 400),
        }
        previous = state["plan"]
        version = previous["version"] if previous else 0
        if previous == {**public, "version": version}:
            return previous
        snapshot = {**public, "version": version + 1}
        self.runtime.emit({"type": "plan.updated", "agent_id": self.agent_id, "span_id": span, "plan": snapshot})
        state["plan"] = snapshot
        return snapshot

    def _finish_plan(self, state: State, span: str, reason: str) -> list[str]:
        plan = state["plan"]
        if plan is None:
            return []
        if any(task["status"] in OPEN_TASK_STATUSES for task in plan["tasks"]):
            tasks = [
                {**task, "status": "blocked", "note": reason} if task["status"] in OPEN_TASK_STATUSES else task
                for task in plan["tasks"]
            ]
            plan = self._publish_plan(state, span, {**plan, "tasks": tasks, "reason": "Record the gaps remaining at the end of the investigation."})
        gaps = [f"{task['question']} · {task['note']}" for task in plan["tasks"] if task["status"] == "blocked"]
        for gap in gaps:
            self.runtime.note(gap)
        return gaps

    def _parse_reply(self, content: str | None) -> str:
        if not content or not content.strip():
            raise LLMError("The model returned an empty reply.")
        return self.runtime.safe_text(content.strip(), MAX_REPLY_CHARS)

    @staticmethod
    def _parse_report(content: str | None) -> Report:
        return Report.model_validate(json_object(content).get("report"))

    def _offline_reply(self, messages: list[dict], span: str) -> str:
        question = messages[-1]["content"].casefold()
        has_channel = any(term in question for term in ("inbound", "outbound", "入站", "出站"))
        earlier_questions = "\n".join(
            message["content"].casefold() for message in messages[:-1] if message["role"] == "user"
        )
        has_topic = any(term in question for term in FREQUENCY_TERMS)
        has_follow_up = has_channel and any(term in earlier_questions for term in FREQUENCY_TERMS)
        if not (has_topic or has_follow_up):
            return "The offline demo answers questions about Frequency Cap. Connect a model gateway to ask about other topics."

        result = self.runtime.call_tool(
            self.agent_id, span, self.runtime.sources.domain.toolset(), "read_document",
            {"page_id": "DEMO-FD2", "version": "1", "section": "Phase 5 / Step 18"},
        )
        if "error" in result:
            return "The Frequency Cap demo document is unavailable."
        content = result["content"].partition("\n")[2].strip()
        if has_channel:
            content = next(
                (paragraph for paragraph in content.split("\n\n") if "Inbound" in paragraph and "Outbound" in paragraph),
                content,
            )
        return f"{content}\n\nSee P5 Step 18 in the demo document DEMO-FD2 v1."

    def _offline_analysis(self, requirement: str, span: str) -> Report:
        normalized = " ".join(requirement.casefold().split()).rstrip(".")
        demo_case = normalized == DEMO_REQUIREMENT.casefold().rstrip(".")
        state: State = {"requirement": requirement, "plan": None, "demo_case": demo_case}
        domain = DomainKnowledgeInvestigator(self.runtime)
        code = CodeInvestigator(self.runtime)

        tasks = [
            {"id": "T1", "agent": "domain", "question": "Verify the Frequency Cap rule and the Mandatory Campaign exemption", "status": "pending"},
            {"id": "T2", "agent": "code", "question": "Locate the Frequency Cap implementation and both channel entry points", "status": "pending"},
        ]

        def show_plan(reason: str) -> None:
            if demo_case:
                self._publish_plan(state, span, {
                    "goal": "Assess the impact of removing the Frequency Cap exemption for Outbound Mandatory Campaigns",
                    "scope": {"Affected channel": "Outbound", "Regression scope": "Existing Inbound behavior", "Campaign": "Mandatory"},
                    "candidate_steps": ["P5 Step 18 · Frequency Cap"], "tasks": tasks, "reason": reason,
                })

        def assignment(index: int) -> dict | None:
            if not demo_case:
                return None
            task = tasks[index]
            return {"task_id": task["id"], "agent": task["agent"], "question": task["question"]}

        show_plan("Start by checking the exemption rule and the code entry points.")
        state["domain"] = [domain.run(state, span, assignment(0))]
        if demo_case:
            pages = {evidence.get("page_id") for evidence in self.runtime.evidence.values()}
            if {"DEMO-FD2", "DEMO-SETTINGS"} <= pages:
                tasks[0]["status"] = "completed"
            tasks[1]["status"] = "in_progress"
            show_plan("The business rule checks have run. Next, check the code paths for both channels.")
        state["code"] = [code.run(state, span, assignment(1))]
        if demo_case and self.runtime.has_tool_budget():
            paths = {evidence.get("path") for evidence in self.runtime.evidence.values()}
            if {"decision/frequency.py", "decision/inbound.py", "decision/outbound.py"} <= paths:
                tasks[1]["status"] = "completed"
            tasks.extend([
                {"id": "T3", "agent": "code", "question": "Check the exemption setting and existing test assertions", "status": "in_progress"},
                {"id": "T4", "agent": "code", "question": "Check how delivery consumes the Outbound candidate list", "status": "pending"},
            ])
            show_plan("Check whether the configuration and tests cover channel isolation, and how blocking candidates affects delivery.")
            code.run(state, span, assignment(2))
            code.run(state, span, assignment(3))
            paths = {evidence.get("path") for evidence in self.runtime.evidence.values()}
            if {"config/global_settings.json", "tests/test_frequency.py"} <= paths:
                tasks[2]["status"] = "completed"
            if "channel/delivery.py" in paths:
                tasks[3]["status"] = "completed"
            show_plan("The configuration, regression test, and delivery source checks have run.")
        self._finish_plan(state, span, TIME_LIMIT if self.runtime.remaining() <= 0 else "The demo did not provide enough evidence to verify this task.")
        return self.offline_report(state)

    def fallback(self, requirement: str) -> Report:
        return Report(
            requirement_summary=requirement,
            summary=(
                "This run gathered partial evidence. See the sources and trace for details."
                if self.runtime.evidence else "This run found no traceable document or code evidence."
            ),
            coverage=[f"Evidence records read · {len(self.runtime.evidence)}."],
            open_questions=list(self.runtime.limits),
        )

    def offline_report(self, state: State) -> Report:
        if not state["demo_case"]:
            self.runtime.note("The offline report covers the built-in example. Connect a model gateway to analyze other requests.")
            return self.fallback(state["requirement"])
        by_page = {row.get("page_id"): key for key, row in self.runtime.evidence.items()}
        by_path = {row.get("path"): key for key, row in self.runtime.evidence.items()}
        fd = by_page.get("DEMO-FD2")
        settings = by_page.get("DEMO-SETTINGS")
        freq = by_path.get("decision/frequency.py")
        inbound = by_path.get("decision/inbound.py")
        outbound = by_path.get("decision/outbound.py")
        config = by_path.get("config/global_settings.json")
        tests = by_path.get("tests/test_frequency.py")
        delivery = by_path.get("channel/delivery.py")

        def refs(*keys):
            return [key for key in keys if key]

        def item(item_id, kind, target, effect, reason, keys, *, verified=False, related=()):
            return {
                "id": item_id, "kind": kind, "target": target, "effect": effect, "reason": reason,
                "evidence_ids": keys, "related_item_ids": list(related),
                "status": "confirmed" if verified else "potential" if keys else "unknown",
            }

        return Report.model_validate({
            "requirement_summary": "Outbound Mandatory Campaigns must respect the Frequency Cap even when the override is enabled.",
            "summary": "The proposed change affects P5 Step 18. Preserve Inbound behavior where both channels use the same implementation.",
            "assumptions": ["This demo uses synthetic data and covers the Outbound Frequency Cap decision."],
            "current_support": {
                "status": "change_required" if fd and freq else "unknown",
                "reason": "The demo code bypasses the cap when the override is enabled." if fd and freq else "More evidence is needed on the rule and its implementation.",
                "evidence_ids": refs(fd, freq),
            },
            "impact_items": [
                item("P5", "process", "P5 Step 18 · Frequency Cap", "direct_change",
                     "Outbound Mandatory Campaigns above the cap would be blocked.", refs(fd, freq), verified=bool(fd and freq)),
                item("Rule", "business_rule", "Mandatory Campaign exemption", "direct_change",
                     "Remove the existing exemption for the Outbound channel.", refs(fd), verified=bool(fd), related=("P5",)),
                item("Code", "code", "Shared Frequency Cap implementation and channel entry points", "verify",
                     "Both channels call the same function. Verify that the change is isolated to Outbound.", refs(freq, inbound, outbound),
                     related=("Rule",)),
                item("Config", "configuration", "Allow mandate campaign override", "verify",
                     "Verify how the override applies to each channel and confirm the separate Quiet Time configuration.",
                     refs(settings, config, freq), related=("Rule",)),
                item("Test", "test", "Frequency Cap regression tests", "verify",
                     "Review existing allow assertions and cover the new Outbound rule and existing Inbound behavior.", refs(tests), related=("Code",)),
                item("Delivery", "downstream", "Outbound candidates and delivery", "indirect_effect",
                     "Blocking campaigns may reduce the candidate list passed to delivery.", refs(outbound, delivery), related=("P5",)),
            ],
            "regression_scenarios": [
                {"name": "Outbound Mandatory above the cap with override enabled", "given": "override=on, cap exceeded",
                 "before": "Allowed", "proposed": "Blocked", "evidence_ids": refs(fd, freq, tests)},
                {"name": "Outbound Mandatory above the cap with override disabled", "given": "override=off, cap exceeded",
                 "before": "Blocked", "proposed": "Blocked", "evidence_ids": refs(freq)},
                {"name": "Outbound Mandatory within the cap", "given": "cap not exceeded",
                 "before": "Allowed", "proposed": "Allowed", "evidence_ids": refs(freq)},
                {"name": "Outbound Marketing above the cap", "given": "cap exceeded",
                 "before": "Blocked", "proposed": "Blocked", "evidence_ids": refs(freq)},
                {"name": "Inbound Mandatory above the cap with override enabled", "given": "override=on, cap exceeded",
                 "before": "Allowed", "proposed": "Allowed", "evidence_ids": refs(inbound, freq, tests)},
            ],
            "coverage": ["Synthetic FD2 and settings pages, and two synthetic repositories."],
            "open_questions": ["Verify later Feature Drops, the deployed version, and channel-specific exceptions against project sources."],
        })
