"""Export Confluence pages through a locally supplied MCP integration."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ConfluencePage:
    page_id: str
    version: str | int
    title: str
    markdown: str
    ancestors: list[str] = field(default_factory=list)
    feature_drop: str | None = None
    source_url: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    links: list[dict | str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


def pull_confluence_pages(*, space_key: str, root_page_ids: list[str]) -> Iterable[ConfluencePage]:
    """Implement this function with the approved intranet Confluence MCP client.

    Resolve the allowed roots, fetch the page body and metadata, convert the
    body to Markdown, and yield one ConfluencePage per version to export.
    """
    raise NotImplementedError("Configure a local Confluence MCP provider with --provider module:function.")


def _atomic_write(path: Path, data: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_export(directory: str | Path, pages: Iterable[ConfluencePage]) -> Path:
    root = Path(directory).resolve()
    (root / "pages").mkdir(parents=True, exist_ok=True)
    rows = []
    seen = set()
    for page in pages:
        page_id, version = str(page.page_id).strip(), str(page.version).strip()
        key = (page_id, version)
        if not all(key) or key in seen or not page.title.strip() or not page.markdown.strip():
            raise ValueError("Each exported page needs a unique page ID/version, title, and Markdown body.")
        seen.add(key)
        content = page.markdown
        filename = hashlib.sha256(f"{page_id}\0{version}\0{content}".encode("utf-8")).hexdigest() + ".md"
        relative = f"pages/{filename}"
        _atomic_write(root / relative, content)
        metadata = asdict(page)
        metadata.pop("markdown")
        rows.append({**metadata, "page_id": page_id, "version": version, "path": relative})
    if not rows:
        raise ValueError("The provider returned no Confluence pages.")
    manifest = root / "manifest.json"
    _atomic_write(
        manifest,
        json.dumps({"documents": rows}, ensure_ascii=False, indent=2) + "\n",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", help="Approved local adapter module and function, written module:function.")
    parser.add_argument("--space", required=True, help="Confluence space key.")
    parser.add_argument("--root-page-id", action="append", default=[], help="Page root; may be repeated.")
    parser.add_argument("--output", required=True, help="Directory for manifest.json and original Markdown.")
    args = parser.parse_args()
    if not args.provider:
        parser.error("Supply --provider module:function for the intranet Confluence connector.")
    module, separator, function = args.provider.partition(":")
    if not separator or not module or not function:
        parser.error("--provider must look like module:function")
    try:
        provider = getattr(importlib.import_module(module), function)
        pages = provider(space_key=args.space, root_page_ids=args.root_page_id)
        manifest = write_export(args.output, pages)
    except Exception as error:
        # Connector exceptions can include authentication headers or internal endpoints.
        parser.exit(1, f"Export failed ({type(error).__name__}). Check the local connector and export permissions.\n")
    print(manifest)


if __name__ == "__main__":
    main()
