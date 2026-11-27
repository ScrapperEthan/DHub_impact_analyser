"""Source backends, with run composition loaded on demand."""

from typing import TYPE_CHECKING

from .codegraph import CodeGraphAdapter as CodeGraphAdapter
from .utils import SourceError as SourceError

if TYPE_CHECKING:
    from .manager import Sources as Sources

__all__ = ["CodeGraphAdapter", "SourceError", "Sources"]


def __getattr__(name: str):
    # Keep the public Sources import while loading tools only at composition time.
    if name == "Sources":
        from .manager import Sources

        return Sources
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
