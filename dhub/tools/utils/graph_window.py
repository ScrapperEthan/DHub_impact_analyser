"""Validate captured CodeGraph result windows."""

from ...codegraph import MAX_GRAPH_READ_CHARS
from .source import SourceError


def validate_graph_window(offset: int, limit: int, max_chars: int) -> None:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 30:
        raise SourceError("invalid_graph_window", "Use offset >= 0 and a relation limit from 1 to 30.")
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_GRAPH_READ_CHARS:
        raise SourceError("invalid_graph_window", f"Use max_chars between 1 and {MAX_GRAPH_READ_CHARS}.")
