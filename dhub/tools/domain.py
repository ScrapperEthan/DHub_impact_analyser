"""Confluence document tools exposed to the Domain Knowledge Investigator."""

from ..documents import Knowledge
from ..source_utils import SourceError, bounded_size, query_tokens
from .registry import ToolDefinition, build_toolset, define_tool


class DomainTools:
    def __init__(self, knowledge: Knowledge):
        self.knowledge = knowledge

    def toolset(self) -> dict[str, ToolDefinition]:
        return build_toolset(
            define_tool(self.search_documents, trace_fields=("query", "feature_drop", "ancestor", "limit")),
            define_tool(self.list_documents, trace_fields=("feature_drop", "ancestor", "limit")),
            define_tool(
                self.read_document,
                trace_fields=("page_id", "version", "section", "offset", "limit"),
                evidence_factory=_document_evidence,
            ),
            define_tool(self.get_document_metadata, trace_fields=("page_id", "version")),
        )

    def list_documents(
        self, feature_drop: str | None = None, ancestor: str | None = None, limit: int = 15
    ) -> dict:
        """Browse available Confluence catalogue entries."""
        self.knowledge.available()
        matches = [
            doc.metadata for doc in self.knowledge.documents.values()
            if _matches_scope(doc.metadata, feature_drop, ancestor)
        ]
        return {"results": matches[:bounded_size(limit, 50)]}

    def search_documents(
        self, query: str, feature_drop: str | None = None, ancestor: str | None = None, limit: int = 8
    ) -> dict:
        """Find relevant Markdown sections by query and optional scope. Search results are discovery hints."""
        self.knowledge.available()
        query = query.strip()
        if not query:
            raise SourceError("invalid_query", "Enter a search query.")
        terms = query_tokens(query)
        hits = []
        for doc in self.knowledge.documents.values():
            if not _matches_scope(doc.metadata, feature_drop, ancestor):
                continue
            lines = doc.content.splitlines()
            sections = doc.sections or [("Document", 0, 0)]
            for index, (section, start, _) in enumerate(sections):
                end = sections[index + 1][1] if index + 1 < len(sections) else len(lines)
                content = "\n".join(lines[start:end])
                labels = " ".join((
                    doc.metadata["title"], section, str(doc.metadata.get("keywords") or ""),
                )).casefold()
                folded = content.casefold()
                score = sum(2 * min(folded.count(term), 3) for term in terms)
                if query.casefold() in folded:
                    score += 6
                if score or index == 0:
                    score += sum(term in labels for term in terms)
                if not score:
                    continue
                snippet = next(
                    (line.strip() for line in lines[start + 1:end]
                     if any(term in line.casefold() for term in terms)),
                    " ".join(lines[start:end]),
                )
                hits.append((score, {
                    **{key: doc.metadata[key] for key in ("page_id", "version", "title", "feature_drop", "source_url")},
                    "section": section, "snippet": snippet[:380], "match_type": "keyword",
                }))
        hits.sort(key=lambda hit: -hit[0])
        return {"results": [hit for _, hit in hits[:bounded_size(limit, 30)]]}

    def get_document_metadata(self, page_id: str, version: str | int) -> dict:
        """Get authoritative catalogue metadata for a page version."""
        return self.knowledge.document(page_id, version).metadata.copy()

    def read_document(
        self, page_id: str, version: str | int, section: str | None = None, offset: int = 0, limit: int = 3500
    ) -> dict:
        """Read an original Markdown section at the specified page version. Use its evidence ID."""
        doc = self.knowledge.document(page_id, version)
        content = doc.content
        if section and section.casefold() != "document":
            for index, (name, start, depth) in enumerate(doc.sections):
                if name.casefold() == section.strip().casefold():
                    lines = content.splitlines(keepends=True)
                    end = next(
                        (position for _, position, next_depth in doc.sections[index + 1:] if next_depth <= depth),
                        len(lines),
                    )
                    content = "".join(lines[start:end])
                    section = name
                    break
            else:
                raise SourceError("section_not_found", "Section was not found. Read the page or list its headings.")
        offset = max(0, offset)
        end = offset + bounded_size(limit, 12_000)
        return {
            "metadata": doc.metadata.copy(), "section": section or "Document",
            "content": content[offset:end], "offset": offset,
            "next_offset": end if end < len(content) else None,
        }


def _matches_scope(metadata: dict, feature_drop: str | None, ancestor: str | None) -> bool:
    return (
        (not feature_drop or metadata["feature_drop"].casefold() == feature_drop.casefold())
        and (not ancestor or any(ancestor.casefold() in item.casefold() for item in metadata["ancestors"]))
    )


def _document_evidence(result: dict) -> dict | None:
    if not result.get("content"):
        return None
    metadata = result["metadata"]
    fields = {key: metadata.get(key) for key in ("page_id", "version", "title", "feature_drop", "source_url")}
    section = result.get("section") or "Full page"
    if result.get("offset"):
        section += f" · Character offset {result['offset']}"
    return {**fields, "source_type": "confluence", "section": section, "excerpt": result["content"]}
