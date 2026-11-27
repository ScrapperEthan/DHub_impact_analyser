"""Validate a Main assignment and return the investigator's findings."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...agent_runtime import AgentRuntime, State
    from ...agents.code import CodeInvestigator
    from ...agents.domain import DomainKnowledgeInvestigator


def run_investigation(
    investigator: DomainKnowledgeInvestigator | CodeInvestigator, question: str, task_id: str | None,
    *, runtime: AgentRuntime, findings: State, span: str, publish_plan: Callable[[dict], dict],
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

    evidence_start = len(runtime.evidence)
    try:
        finding = investigator.run(findings, span, assignment)
    finally:
        if plan is not None:
            plan = publish_plan(plan)
    findings[investigator.agent_id].append(finding)
    evidence = list(runtime.evidence.values())[evidence_start:]
    return {
        "findings": finding,
        "evidence": [
            {
                **entry, "excerpt": entry["excerpt"][:1200],
                **({"excerpt_truncated": True} if len(entry["excerpt"]) > 1200 else {}),
            }
            for entry in evidence
        ],
        "limitations": runtime.limits,
        **({"task_id": task_id, "plan": plan} if plan is not None else {}),
    }
