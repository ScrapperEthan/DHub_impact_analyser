"""Rank and locate Markdown sections in the current snapshot."""

import re

from ..documents import Document, Section
from .utils.document_scope import document_coverage, matches_scope
from .utils.paging import bounded_size, search_page
from .utils.search import match_excerpt, query_tokens
from .utils.source import SourceError


TRACE_FIELDS = ("query", "feature_drop", "ancestor", "page_id", "version", "limit")


def search_documents(
    self, query: str, feature_drop: str | None = None, ancestor: str | None = None, limit: int = 8,
    page_id: str | None = None, version: str | int | None = None,
) -> dict:
    """Find Markdown sections by keywords and phrase relevance, optionally within a page/version.

    Use page_id, version and section_id from a hit to read the original text.
    """
    query = " ".join(query.split())
    if not query or "\0" in query or len(query) > 240:
        raise SourceError("invalid_query", "Use a non-empty document query up to 240 characters.")
    page_id = page_id.strip() if page_id else None
    version = str(version).strip() if version is not None else None
    if version is not None and not page_id:
        raise SourceError("invalid_scope", "Specify page_id when filtering by version.")
    limit = bounded_size(limit, 30)
    scope = {"feature_drop": feature_drop, "ancestor": ancestor, "page_id": page_id, "version": version, "limit": limit}
    status, warnings = document_coverage(self.knowledge)
    unavailable = [{"code": "knowledge_unavailable", "message": warnings[0]}] if status == "unavailable" else []
    terms = query_tokens(query)
    folded_query = query.casefold()
    hits = []
    for doc in self.knowledge.documents.values():
        metadata = doc.metadata
        if (
            (page_id and metadata["page_id"] != page_id)
            or (version is not None and metadata["version"] != version)
            or not matches_scope(metadata, feature_drop, ancestor)
        ):
            continue
        page_score = (
            12 * (folded_query in doc.folded_title)
            + 2 * len(terms & doc.title_terms)
            + len(terms & doc.keyword_terms)
        )
        for section in doc.sections.values():
            local_score = (
                24 * (folded_query in section.folded_heading)
                + 16 * (folded_query in section.folded_text)
                + 6 * len(terms & section.heading_terms)
                + 4 * len(terms & section.terms)
            )
            if local_score or (page_score and section.section_id == doc.primary_section):
                hits.append((local_score + page_score, doc, section))
    hits.sort(key=lambda hit: -hit[0])
    phrase = re.compile(re.escape(query), re.IGNORECASE)
    words = "|".join(_keyword_expression(term) for term in sorted(terms, key=lambda term: (-len(term), term)))
    keyword = re.compile(words, re.IGNORECASE) if words else None
    results = [_document_hit(doc, section, phrase, keyword) for _, doc, section in hits[:limit + 1]]
    return search_page(results, limit, status=status, scope=scope, warnings=warnings, unavailable=unavailable)


def _keyword_expression(term: str) -> str:
    escaped = re.escape(term)
    if re.fullmatch(r"[\u3400-\u9fff]+", term):
        return escaped
    return rf"(?<![^\W_]){escaped}(?![^\W_])"


def _document_hit(doc: Document, section: Section, phrase: re.Pattern, keyword: re.Pattern | None) -> dict:
    match_line = None
    snippet = doc.metadata["title"][:380]
    for pattern in (phrase, keyword):
        if not pattern:
            continue
        for index in range(section.start, section.search_end):
            text = doc.lines[index].strip()
            if match := pattern.search(text):
                match_line = index + 1
                snippet = match_excerpt(text, match.start(), match.end(), 380)
                break
        if match_line is not None:
            break
    return {
        **{key: doc.metadata[key] for key in ("page_id", "version", "title", "feature_drop", "source_url")},
        "section": section.path, "section_path": section.path, "section_id": section.section_id,
        "parent_section_id": section.parent_id,
        "start_line": section.start + 1 if section.search_end > section.start else None,
        "end_line": section.search_end or None, "read_end_line": section.read_end or None,
        "match_line": match_line, "snippet": snippet, "match_type": "keyword",
    }
