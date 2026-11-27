"""Query static relationships in a pinned CodeGraph index."""

import re

from pydantic import BaseModel, ConfigDict, Field

from ..codegraph import DEFAULT_GRAPH_READ_CHARS, MAX_GRAPH_READ_CHARS
from .utils.graph_window import validate_graph_window
from .utils.source import SourceError


TRACE_FIELDS = ("repo_id", "limit", "max_chars")
MAX_GRAPH_QUERY_CHARS = 1_200


class GraphExploreArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(min_length=1, description="Repository ID from the pinned snapshot.")
    query: str = Field(min_length=1, max_length=MAX_GRAPH_QUERY_CHARS, description="Symbol or static relationship question.")
    limit: int = Field(default=6, strict=True, ge=1, le=30, description="Maximum relation rows per window.")
    max_chars: int = Field(default=DEFAULT_GRAPH_READ_CHARS, strict=True, ge=1, le=MAX_GRAPH_READ_CHARS)


def codegraph_explore(
    self, repo_id: str, query: str, limit: int = 6, max_chars: int = DEFAULT_GRAPH_READ_CHARS,
) -> dict:
    """Query static relationships at the pinned index. Use result_id and next_offset to continue."""
    validate_graph_window(0, limit, max_chars)
    if (
        not query.strip() or len(query) > MAX_GRAPH_QUERY_CHARS
        or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]", query)
    ):
        raise SourceError("invalid_query", f"Use a CodeGraph query up to {MAX_GRAPH_QUERY_CHARS} characters.")
    return self.graph.explore(self._repo(repo_id), query.strip(), limit, max_chars)
