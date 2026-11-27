"""Look up metadata by Confluence page and version."""

TRACE_FIELDS = ("page_id", "version")


def get_document_metadata(self, page_id: str, version: str | int) -> dict:
    """Get authoritative catalogue metadata for a page version."""
    return self.knowledge.document(page_id, version).metadata.copy()
