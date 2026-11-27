"""Let Main assign a code and dependency investigation."""

from collections.abc import Callable

from ..planning import TaskId
from .registry import ToolDefinition, define_tool


def create_investigate_code_tool(delegate: Callable[[str, str | None], dict]) -> ToolDefinition:
    def investigate_code(question: str, task_id: TaskId | None = None) -> dict:
        """Check DHub code, configuration and tests. Include task_id when an explicit plan exists."""
        return delegate(question, task_id)

    return define_tool(investigate_code)
