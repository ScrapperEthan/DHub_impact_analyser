"""Continue a captured CodeGraph result within the same run."""

from pydantic import BaseModel, ConfigDict, Field

from ..codegraph import DEFAULT_GRAPH_READ_CHARS, MAX_GRAPH_READ_CHARS
from .utils.graph_window import validate_graph_window


TRACE_FIELDS = ("offset", "limit", "max_chars")


class GraphReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str = Field(min_length=1, max_length=80, description="Result handle from this run.")
    offset: int = Field(default=0, strict=True, ge=0, description="Use the previous next_offset in the same result.")
    limit: int = Field(default=6, strict=True, ge=1, le=30)
    max_chars: int = Field(default=DEFAULT_GRAPH_READ_CHARS, strict=True, ge=1, le=MAX_GRAPH_READ_CHARS)


def codegraph_read_result(
    self, result_id: str, offset: int = 0, limit: int = 6, max_chars: int = DEFAULT_GRAPH_READ_CHARS,
) -> dict:
    """Read the next captured window. Offset counts relation rows or text characters by result_format."""
    validate_graph_window(offset, limit, max_chars)
    return self.graph.read_result(result_id, offset, limit, max_chars)
