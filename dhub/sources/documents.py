"""Versioned Confluence Markdown snapshots and heading lookup."""

import hashlib
import json
import logging
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import Path

from .utils import SourceError, path_in_source, query_tokens, read_source_text

log = logging.getLogger(__name__)
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(\S.*)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
MAX_MARKDOWN_BYTES = 8_000_000


def _page_key(page_id: str, version: str | int) -> tuple[str, str]:
    return str(page_id).strip(), str(version).strip()


@dataclass
class Section:
    section_id: str
    path: str
    parent_id: str | None
    start: int
    search_end: int
    read_end: int
    folded_heading: str = ""
    folded_text: str = field(init=False)
    terms: set[str] = field(init=False)
    heading_terms: set[str] = field(init=False)


def _sections(lines: list[str]) -> list[Section]:
    sections: list[Section] = []
    stack: list[tuple[int, str, Section]] = []
    fence = ""
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        fence_match = FENCE.match(line)
        if fence_match:
            marker, trailing = fence_match.groups()
            if not fence:
                fence = marker
            elif marker.startswith(fence) and not trailing.strip():
                fence = ""
            continue
        heading = HEADING.match(line) if not fence else None
        if not heading:
            continue
        depth = len(heading[1])
        name = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2]).rstrip()
        while stack and stack[-1][0] >= depth:
            stack.pop()[2].read_end = index
        if not sections and index:
            sections.append(Section("sec-1", "Preamble", None, 0, index, index))
        if sections:
            sections[-1].search_end = index
        # Keep the existing display path without the H1 page title.
        parents = [label for level, label, _ in stack if level > 1]
        path = " / ".join([*parents, name])
        section = Section(
            f"sec-{index + 1}", path, stack[-1][2].section_id if stack else None,
            index, len(lines), len(lines), path.casefold(),
        )
        sections.append(section)
        stack.append((depth, name, section))
    return sections or [Section("sec-1", "Document", None, 0, len(lines), len(lines))]


@dataclass
class Document:
    metadata: dict
    content: str
    lines: list[str] = field(init=False)
    line_offsets: list[int] = field(init=False)
    sections: dict[str, Section] = field(init=False)
    primary_section: str = field(init=False)
    folded_title: str = field(init=False)
    title_terms: set[str] = field(init=False)
    keyword_terms: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.lines = self.content.splitlines(keepends=True)
        self.line_offsets = list(accumulate(map(len, self.lines), initial=0))
        sections = _sections(self.lines)
        headings = [section for section in sections if section.folded_heading]
        self.metadata["headings"] = [section.path for section in headings]
        self.primary_section = (headings or sections)[0].section_id
        self.folded_title = self.metadata["title"].casefold()
        self.title_terms = query_tokens(self.folded_title)
        self.keyword_terms = query_tokens(str(self.metadata.get("keywords") or ""))
        for section in sections:
            start, end = self.line_offsets[section.start], self.line_offsets[section.search_end]
            section.folded_text = self.content[start:end].casefold()
            section.terms = query_tokens(section.folded_text)
            section.heading_terms = query_tokens(section.folded_heading)
        self.sections = {section.section_id: section for section in sections}

    def section(self, name: str | None, section_id: str | None) -> Section | None:
        if section_id is not None:
            section = self.sections.get(section_id)
            if section is None:
                raise SourceError("section_not_found", "Use a section_id from search_documents for this page and version.")
            return section
        if not name or name.strip().casefold() == "document":
            return None
        matches = [section for section in self.sections.values() if section.path.casefold() == name.strip().casefold()]
        if len(matches) > 1:
            raise SourceError("ambiguous_section", "Multiple sections share that name. Search this page and use section_id.")
        if not matches:
            raise SourceError("section_not_found", "Section was not found. Search this page or read the full page.")
        return matches[0]

    def lines_for_window(self, start: int, end: int) -> tuple[int | None, int | None]:
        if start >= end:
            return None, None
        return bisect_right(self.line_offsets, start), bisect_left(self.line_offsets, end)


def _manifest_problem(error: Exception) -> str:
    if isinstance(error, SourceError):
        return str(error)
    if isinstance(error, FileNotFoundError):
        return "The manifest file was not found."
    if isinstance(error, OSError):
        return f"The manifest is not a readable file ({type(error).__name__}). Point to the manifest JSON file."
    if isinstance(error, UnicodeError):
        return "The manifest is not UTF-8 text."
    if isinstance(error, json.JSONDecodeError):
        return f"The manifest is not valid JSON (line {error.lineno}, column {error.colno})."
    return "The manifest needs a documents array."


def _page_problem(error: Exception, path) -> str:
    location = str(path)[:200]
    if isinstance(error, SourceError):
        return f"{error} Path: {location}"
    if isinstance(error, FileNotFoundError):
        return f"Markdown file not found: {location}"
    if isinstance(error, OSError):
        return f"Markdown file could not be read ({type(error).__name__}): {location}"
    if isinstance(error, UnicodeError):
        return f"Markdown file is not UTF-8 text: {location}"
    if isinstance(error, KeyError):
        return "The manifest row needs a Markdown path."
    return str(error) or type(error).__name__


class Knowledge:
    def __init__(self, manifest: Path | None):
        self.documents: dict[tuple[str, str], Document] = {}
        self.snapshot_id: str | None = None
        self.error: str | None = None
        self.skipped_pages: list[dict] = []
        if manifest is None:
            self.error = "Configure a local Markdown export manifest."
            return
        try:
            raw = read_source_text(manifest, MAX_MARKDOWN_BYTES)
            data = json.loads(raw)
            rows = data["documents"]
            if not isinstance(rows, list):
                raise ValueError("documents must be an array")
        except (OSError, ValueError, TypeError, KeyError, SourceError) as error:
            self.error = f"The Markdown export manifest is missing or invalid. {_manifest_problem(error)}"
            log.warning("%s Manifest: %s", self.error, manifest)
            return

        digest = hashlib.sha256(raw.encode("utf-8"))
        for row_number, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                self.skipped_pages.append({"row": row_number, "reason": "The manifest row is not an object."})
                continue
            key = _page_key(row.get("page_id") or "", row.get("version") or "")
            try:
                if not all(key):
                    raise ValueError("The manifest row needs page_id and version.")
                if key in self.documents:
                    raise ValueError("The page_id and version already appear in an earlier row.")
                source = path_in_source(manifest.parent, row["path"])
                content = read_source_text(source, MAX_MARKDOWN_BYTES)
                ancestors = row.get("ancestors") or []
                if not isinstance(ancestors, list) or not all(isinstance(item, str) for item in ancestors):
                    raise ValueError("ancestors must be a list of strings.")
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
            except (OSError, ValueError, TypeError, KeyError, SourceError) as error:
                self.skipped_pages.append({
                    "row": row_number, "page_id": key[0] or None, "version": key[1] or None,
                    "reason": _page_problem(error, row.get("path")),
                })
        if self.skipped_pages:
            log.warning(
                "Skipped %d manifest rows. First reasons: %s", len(self.skipped_pages),
                "; ".join(f"row {page['row']}: {page['reason']}" for page in self.skipped_pages[:3]),
            )
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
            "skipped_count": len(self.skipped_pages),
            "skipped_pages": self.skipped_pages[:50],
            **({"error": self.error} if self.error else {}),
        }
