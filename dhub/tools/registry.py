"""Bind tool callables to their model schemas and optional evidence extraction."""

from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.tools import StructuredTool
from pydantic import BaseModel


@dataclass(frozen=True)
class ToolDefinition:
    tool: StructuredTool
    trace_fields: tuple[str, ...]
    evidence_factory: Callable[[dict], dict | None] | None


def define_tool(
    handler: Callable[..., dict],
    *,
    trace_fields: tuple[str, ...] = (),
    evidence_factory: Callable[[dict], dict | None] | None = None,
    args_schema: type[BaseModel] | None = None,
) -> ToolDefinition:
    tool = StructuredTool.from_function(func=handler, args_schema=args_schema)
    unknown_fields = set(trace_fields) - tool.args.keys()
    if unknown_fields:
        fields = ", ".join(sorted(unknown_fields))
        raise ValueError(f"Unknown Trace fields for {tool.name}: {fields}")
    return ToolDefinition(tool=tool, trace_fields=trace_fields, evidence_factory=evidence_factory)


def build_toolset(*definitions: ToolDefinition) -> dict[str, ToolDefinition]:
    tools = {definition.tool.name: definition for definition in definitions}
    if len(tools) != len(definitions):
        raise ValueError("Tool names must be unique within an agent.")
    return tools
