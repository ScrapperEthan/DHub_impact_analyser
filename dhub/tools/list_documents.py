"""Browse a snapshot's Confluence catalogue."""

from .utils.document_scope import document_coverage, matches_scope
from .utils.paging import bounded_size, catalogue_page


TRACE_FIELDS = ("feature_drop", "ancestor", "offset", "limit")


def list_documents(
    self, feature_drop: str | None = None, ancestor: str | None = None, limit: int = 15, offset: int = 0,
) -> dict:
    """Browse the Confluence catalogue by scope. Use next_offset to fetch the next page."""
    matches = [
        doc.metadata for doc in self.knowledge.documents.values()
        if matches_scope(doc.metadata, feature_drop, ancestor)
    ]
    status, warnings = document_coverage(self.knowledge)
    return {
        "status": status, "warnings": warnings,
        **catalogue_page(matches, offset, bounded_size(limit, 50)),
    }
