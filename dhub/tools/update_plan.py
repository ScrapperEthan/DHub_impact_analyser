"""Publish Main's investigation plan with stable task identities."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..planning import InvestigationPlan, PlanTask
from .registry import ToolDefinition, define_tool

if TYPE_CHECKING:
    from ..agent_runtime import State


def create_update_plan_tool(
    findings: State, task_agents: dict[str, str], safe_text: Callable[[str, int], str],
    publish_plan: Callable[[dict], dict],
) -> ToolDefinition:
    def update_plan(
        goal: str, tasks: list[PlanTask], reason: str,
        scope: dict[str, str] | None = None, candidate_steps: list[str] | None = None,
    ) -> dict:
        """Create or replace the full public investigation plan. Return the current version and tasks."""
        for task in tasks:
            if safe_text(task.id, 32) != task.id or task_agents.get(task.id, task.agent) != task.agent:
                return {
                    "error": "invalid_task_id", "plan": findings["plan"],
                    "message": "Use a public, stable task ID. Use a new ID if the assigned investigator changes.",
                }
        plan = {
            "goal": goal, "scope": scope or {}, "candidate_steps": candidate_steps or [],
            "tasks": [task.model_dump(exclude_none=True) for task in tasks], "reason": reason,
        }
        snapshot = publish_plan(plan)
        task_agents.update({task.id: task.agent for task in tasks})
        return {"plan": snapshot}

    return define_tool(update_plan, args_schema=InvestigationPlan)
