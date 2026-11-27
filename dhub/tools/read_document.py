"""Read a versioned Markdown window and record document evidence."""

from .utils.paging import bounded_size


TRACE_FIELDS = ("page_id", "version", "section", "section_id", "offset", "limit")


def read_document(
    self, page_id: str, version: str | int, section: str | None = None, offset: int = 0, limit: int = 3500,
    section_id: str | None = None,
) -> dict:
    """Read original Markdown and cite its evidence ID. Prefer the hit's section_id to a heading name.

    Parent sections include their children. Use next_offset for the next window in the selected section.
    """
    doc = self.knowledge.document(page_id, version)
    selection = doc.section(section, section_id)
    scope_start = doc.line_offsets[selection.start] if selection else 0
    scope_end = doc.line_offsets[selection.read_end] if selection else len(doc.content)
    offset = max(0, offset)
    start = min(scope_start + offset, scope_end)
    end = min(start + bounded_size(limit, 12_000), scope_end)
    start_line, end_line = doc.lines_for_window(start, end)
    return {
        "metadata": doc.metadata.copy(), "section": selection.path if selection else "Document",
        "section_id": selection.section_id if selection else None,
        "start_line": start_line, "end_line": end_line,
        "content": doc.content[start:end], "offset": offset, "end_offset": end - scope_start,
        "truncated": end < scope_end,
        "next_offset": end - scope_start if end < scope_end else None,
    }


def document_evidence(result: dict) -> dict | None:
    if not result.get("content"):
        return None
    metadata = result["metadata"]
    fields = {key: metadata.get(key) for key in ("page_id", "version", "title", "feature_drop", "source_url")}
    section = result.get("section") or "Full page"
    if result.get("offset"):
        section += f" · Character offset {result['offset']}"
    return {
        **fields, "source_type": "confluence", "section": section, "excerpt": result["content"],
        **{key: result[key] for key in ("section_id", "start_line", "end_line", "offset", "end_offset")},
    }
