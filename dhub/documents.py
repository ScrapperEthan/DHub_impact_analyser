"""Versioned Confluence Markdown snapshots and heading lookup."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .source_utils import SourceError, path_in_source, read_source_text

HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
MAX_MARKDOWN_BYTES = 8_000_000


def _page_key(page_id: str, version: str | int) -> tuple[str, str]:
    return str(page_id).strip(), str(version).strip()


def _sections(content: str) -> list[tuple[str, int, int]]:
    headings: list[tuple[str, int, int]] = []
    stack: list[tuple[int, str]] = []
    fence = ""
    for index, line in enumerate(content.splitlines()):
        left = line.lstrip()
        if left.startswith(("~~~", "```")):
            marker = left[:3]
            if not fence:
                fence = marker
            elif marker == fence:
                fence = ""
            continue
        match = HEADING.match(line)
        if not match or fence:
            continue
        depth = len(match[1])
        while stack and stack[-1][0] >= depth:
            stack.pop()
        # The first H1 is typically the document title rather than a section label.
        stack.append((depth, match[2]))
        path = " / ".join(name for level, name in stack if level > 1) or match[2]
        headings.append((path, index, depth))
    return headings


@dataclass
class Document:
    metadata: dict
    content: str
    sections: list[tuple[str, int, int]] = field(init=False)

    def __post_init__(self) -> None:
        self.sections = _sections(self.content)
        self.metadata["headings"] = [name for name, _, _ in self.sections]


class Knowledge:
    def __init__(self, manifest: Path | None):
        self.documents: dict[tuple[str, str], Document] = {}
        self.snapshot_id: str | None = None
        self.error: str | None = None
        self.skipped_pages: list[str] = []
        if manifest is None:
            self.error = "Configure a local Markdown export manifest."
            return
        try:
            raw = read_source_text(manifest, MAX_MARKDOWN_BYTES)
            data = json.loads(raw)
            rows = data["documents"]
            if not isinstance(rows, list):
                raise ValueError("documents must be an array")
        except (OSError, ValueError, TypeError, KeyError, SourceError):
            self.error = "The Markdown export manifest is missing or invalid."
            return

        digest = hashlib.sha256(raw.encode("utf-8"))
        for row in rows:
            if not isinstance(row, dict):
                self.skipped_pages.append("(invalid row)")
                continue
            key = _page_key(row.get("page_id") or "", row.get("version") or "")
            try:
                if not all(key) or key in self.documents:
                    raise ValueError("page identity")
                source = path_in_source(manifest.parent, row["path"])
                content = read_source_text(source, MAX_MARKDOWN_BYTES)
                ancestors = row.get("ancestors") or []
                if not isinstance(ancestors, list) or not all(isinstance(item, str) for item in ancestors):
                    raise ValueError("ancestors")
                feature_drop = row.get("feature_drop") or ""
                if not feature_drop:
                    match = re.search(r"\bFD[1-5]\b", " ".join(ancestors), re.IGNORECASE)
                    feature_drop = match[0].upper() if match else ""
                metadata = {
                    **{name: row.get(name) for name in (
                        "source_url", "updated_at", "updated_by", "attachments", "links", "keywords"
                    )},
                    "page_id": key[0],
                    "version": key[1],
                    "title": str(row.get("title") or key[0]),
                    "feature_drop": str(feature_drop),
                    "ancestors": ancestors,
                }
                self.documents[key] = Document(metadata, content)
                digest.update(content.encode("utf-8"))
            except (OSError, ValueError, TypeError, KeyError, SourceError):
                self.skipped_pages.append(key[0] or "(missing page ID)")
        if self.documents:
            self.snapshot_id = digest.hexdigest()
        else:
            self.error = "The Markdown export contains no readable pages."

    def available(self) -> None:
        if self.error:
            raise SourceError("knowledge_unavailable", self.error)

    def document(self, page_id: str, version: str | int) -> Document:
        self.available()
        try:
            return self.documents[_page_key(page_id, version)]
        except KeyError:
            raise SourceError("document_not_found", "Page and version were not found in this snapshot.") from None

    def snapshot(self) -> dict:
        return {
            "status": "unavailable" if self.error else ("partial" if self.skipped_pages else "available"),
            "document_count": len(self.documents),
            "feature_drops": sorted({doc.metadata["feature_drop"] for doc in self.documents.values()} - {""}),
            "skipped_pages": self.skipped_pages[:50],
            **({"error": self.error} if self.error else {}),
        }
