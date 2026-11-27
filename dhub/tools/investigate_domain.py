"""Let Main assign a business-rule investigation."""

from collections.abc import Callable

from ..agents.planning import TaskId
from .registry import ToolDefinition, define_tool


def create_investigate_domain_tool(delegate: Callable[[str, str | None], dict]) -> ToolDefinition:
    def investigate_domain(question: str, task_id: TaskId | None = None) -> dict:
        """Check DHub business rules, steps and exceptions. Include task_id when an explicit plan exists."""
        return delegate(question, task_id)

    return define_tool(investigate_domain)
