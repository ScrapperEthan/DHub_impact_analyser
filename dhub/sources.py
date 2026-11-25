"""Read-only document and repository adapters for one analysis run."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4

DEMO_ROOT = Path(__file__).resolve().parent.parent / "demo"
CODE_EXTENSIONS = frozenset({".py", ".pyi", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql", ".md", ".txt"})
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
MAX_CODE_BYTES = 512_000
MAX_MARKDOWN_BYTES = 8_000_000


class SourceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CodeGraphAdapter(Protocol):
    """Bind graph evidence to the Git commit the index represents."""

    def indexed_commit(self, repo_id: str) -> str | None: ...

    def explore(self, *, repo_id: str, commit_sha: str, query: str) -> list[dict]: ...


def _safe_relative(value: str) -> str:
    value = str(value).replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not path.parts or path.is_absolute() or ".." in path.parts
        or ":" in value or any(ord(char) < 32 for char in value)
    ):
        raise SourceError("invalid_path", "Use a relative path inside the configured source.")
    return path.as_posix()


def _under(root: Path, relative: str) -> Path:
    candidate = (root / _safe_relative(relative)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise SourceError("invalid_path", "Path is outside the configured source.")
    return candidate


def _read_text_file(path: Path, max_bytes: int) -> str:
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SourceError("source_too_large", "Source exceeds the configured file size limit.")
    return data.decode("utf-8-sig")


def _page_key(page_id: str, version: str | int) -> tuple[str, str]:
    return str(page_id).strip(), str(version).strip()


def _size(value: int, maximum: int) -> int:
    return min(max(int(value), 1), maximum)


def _tokens(query: str) -> set[str]:
    tokens = set()
    for word in re.findall(r"[^\W_]+", query.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", word) and len(word) > 2:
            tokens.update(word[index:index + 2] for index in range(len(word) - 1))
        else:
            tokens.add(word)
    return tokens


def _matches_scope(metadata: dict, feature_drop: str | None, ancestor: str | None) -> bool:
    return (
        (not feature_drop or metadata["feature_drop"].casefold() == feature_drop.casefold())
        and (not ancestor or any(ancestor.casefold() in item.casefold() for item in metadata["ancestors"]))
    )


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
            raw = _read_text_file(manifest, MAX_MARKDOWN_BYTES)
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
                source = _under(manifest.parent, row["path"])
                content = _read_text_file(source, MAX_MARKDOWN_BYTES)
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


def _git(root: Path, *args: str, allow_no_matches: bool = False) -> bytes:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            capture_output=True, timeout=20, env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        raise SourceError("source_timeout", "The read-only Git operation timed out.") from None
    except OSError:
        raise SourceError("repository_unavailable", "Git is not available for the repository.") from None
    if result.returncode == 0 or (allow_no_matches and result.returncode == 1):
        return result.stdout
    raise SourceError("git_read_failed", "The requested Git object or configured revision is unavailable.")


@dataclass
class Repository:
    repo_id: str
    root: Path
    ref: str
    synthetic: bool = False
    branch: str | None = None
    commit_sha: str | None = None
    files: dict[str, tuple[str, int] | str] = field(default_factory=dict)
    error: str | None = None

    def pin(self) -> None:
        try:
            if not self.root.is_dir():
                raise SourceError("repository_unavailable", "Configured repository directory is missing.")
            if self.synthetic:
                digest = hashlib.sha256()
                for item in sorted(self.root.rglob("*")):
                    relative = item.relative_to(self.root).as_posix()
                    if not item.is_file() or not _code_path(relative):
                        continue
                    try:
                        content = _read_text_file(_under(self.root, relative), MAX_CODE_BYTES)
                    except (OSError, ValueError, SourceError):
                        continue
                    self.files[relative] = content
                    digest.update(relative.encode("utf-8") + b"\0" + content.encode("utf-8") + b"\0")
                if not self.files:
                    raise SourceError("repository_unavailable", "The synthetic repository contains no readable files.")
                self.commit_sha = digest.hexdigest()
                return
            git_root = Path(_git(self.root, "rev-parse", "--show-toplevel").decode("utf-8").strip())
            if git_root.resolve() != self.root.resolve():
                raise SourceError("repository_unavailable", "Configure the root of the Git checkout.")
            self.commit_sha = _git(
                self.root, "rev-parse", "--verify", "--end-of-options", f"{self.ref}^{{commit}}"
            ).decode("ascii").strip()
            try:
                branch = _git(
                    self.root, "rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", self.ref
                ).decode("utf-8").strip()
                if branch.startswith("refs/heads/"):
                    self.branch = branch.removeprefix("refs/heads/")
            except (SourceError, ValueError):
                pass
            tree = _git(self.root, "ls-tree", "-r", "-l", "-z", "--full-tree", self.commit_sha)
            for entry in tree.split(b"\0"):
                if not entry:
                    continue
                attrs, raw_path = entry.split(b"\t", 1)
                mode, kind, oid, length = attrs.decode("ascii").split()
                path = raw_path.decode("utf-8", errors="replace")
                if mode in {"100644", "100755"} and kind == "blob" and _code_path(path):
                    self.files[path] = (oid, int(length))
        except (OSError, ValueError, SourceError) as error:
            self.commit_sha = None
            self.error = str(error) if isinstance(error, SourceError) else "The repository snapshot could not be read."

    def metadata(self, indexed_commit: str | None) -> dict:
        return {
            "repo_id": self.repo_id,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "codegraph_commit_sha": indexed_commit,
            "snapshot_kind": "synthetic" if self.synthetic else "git",
            "status": "unavailable" if self.error else "available",
            **({"error": self.error} if self.error else {}),
        }

    def read(self, path: str) -> str:
        if path not in self.files:
            raise SourceError("file_not_found", "File is outside the readable paths of this snapshot.")
        entry = self.files[path]
        if isinstance(entry, str):
            return entry
        oid, length = entry
        if length > MAX_CODE_BYTES:
            raise SourceError("source_too_large", "Source exceeds the configured file size limit.")
        blob = _git(self.root, "cat-file", "blob", oid)
        try:
            content = blob.decode("utf-8-sig")
        except UnicodeError:
            raise SourceError("unsupported_encoding", "This source is not UTF-8 text.") from None
        if "\0" in content:
            raise SourceError("unsupported_encoding", "This source is not UTF-8 text.")
        return content

    def search(self, queries: list[str], prefix: str, extensions: set[str], limit: int) -> list[dict]:
        matches = []
        if self.synthetic:
            rows = (
                (path, line, text)
                for path, content in self.files.items()
                if _search_path(path, prefix, extensions)
                for line, text in enumerate(str(content).splitlines(), 1)
                if any(query.casefold() in text.casefold() for query in queries)
            )
        else:
            args = [part for query in queries for part in ("-e", query)]
            output = _git(
                self.root, "grep", "--no-textconv", "-I", "-i", "-F", "-n", "-z", "--full-name",
                f"--max-count={limit}", *args, self.commit_sha, "--",
                *(f":(glob)**/*{extension}" for extension in sorted(extensions)),
                allow_no_matches=True,
            )
            rows = self._grep_rows(output, prefix, extensions)
        for path, line, text in rows:
            matches.append({
                "repo_id": self.repo_id, "branch": self.branch, "commit_sha": self.commit_sha,
                "path": path, "line": line, "text": text[:400],
            })
            if len(matches) == limit:
                break
        return matches

    def _grep_rows(self, output: bytes, prefix: str, extensions: set[str]):
        tree_prefix = f"{self.commit_sha}:"
        for row in output.split(b"\n"):
            parts = row.split(b"\0", 2)
            if len(parts) != 3:
                continue
            name, line, text = (part.decode("utf-8", errors="replace") for part in parts)
            if not name.startswith(tree_prefix) or not line.isdigit():
                continue
            path = name.removeprefix(tree_prefix)
            if path in self.files and _search_path(path, prefix, extensions):
                yield path, int(line), text


def _code_path(path: str) -> bool:
    try:
        parts = PurePosixPath(_safe_relative(path)).parts
    except SourceError:
        return False
    return all(part != ".git" for part in parts) and PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS


def _search_path(path: str, prefix: str, extensions: set[str]) -> bool:
    return (
        (not prefix or path == prefix or path.startswith(prefix + "/"))
        and PurePosixPath(path).suffix.lower() in extensions
    )


class DemoCodeGraph:
    def __init__(self, repositories: dict[str, Repository]):
        self.repositories = repositories
        try:
            self.edges = json.loads(_read_text_file(DEMO_ROOT / "codegraph.json", MAX_MARKDOWN_BYTES))
        except (ValueError, OSError, SourceError):
            self.edges = {}

    def indexed_commit(self, repo_id: str) -> str | None:
        return self.repositories[repo_id].commit_sha if repo_id in self.edges else None

    def explore(self, *, repo_id: str, commit_sha: str, query: str) -> list[dict]:
        terms = _tokens(query)
        matches = []
        for edge in self.edges.get(repo_id, []):
            label = " ".join(str(edge.get(key, "")) for key in ("caller", "callee", "path")).casefold()
            if any(term in label for term in terms):
                matches.append(edge)
        return matches


class Sources:
    """Construct one instance per analysis run to isolate snapshots and graph results."""

    def __init__(
        self,
        *,
        data_mode: str = "demo",
        manifest_path: str | Path | None = None,
        repositories: list[dict] | None = None,
        codegraph: CodeGraphAdapter | None = None,
    ):
        if data_mode not in {"demo", "project"}:
            raise ValueError("data_mode must be demo or project")
        if data_mode == "demo":
            manifest_path = DEMO_ROOT / "manifest.json"
            repositories = [
                {"repo_id": repo_id, "path": DEMO_ROOT / repo_id}
                for repo_id in ("fixture-decision", "fixture-channel")
            ]
        self.knowledge = Knowledge(Path(manifest_path).resolve() if manifest_path else None)
        self.repositories = {}
        for entry in repositories or []:
            repo = Repository(
                repo_id=entry["repo_id"], root=Path(entry["path"]).resolve(),
                ref=entry.get("ref") or "HEAD", synthetic=data_mode == "demo",
            )
            if repo.repo_id in self.repositories:
                raise ValueError("Repository IDs must be unique")
            repo.pin()
            self.repositories[repo.repo_id] = repo
        self.codegraph = DemoCodeGraph(self.repositories) if data_mode == "demo" else codegraph
        self.graph_results: dict[str, tuple[str, str, list[dict]]] = {}

    def _indexed_commit(self, repo_id: str) -> str | None:
        if self.codegraph is None:
            return None
        try:
            return self.codegraph.indexed_commit(repo_id)
        except Exception:
            return None

    def snapshot(self) -> dict:
        return {
            "knowledge_snapshot_id": self.knowledge.snapshot_id,
            "knowledge": self.knowledge.snapshot(),
            "repositories": [
                repo.metadata(self._indexed_commit(repo.repo_id)) for repo in self.repositories.values()
            ],
        }

    def list_documents(
        self, feature_drop: str | None = None, ancestor: str | None = None, limit: int = 15
    ) -> dict:
        self.knowledge.available()
        matches = [
            doc.metadata for doc in self.knowledge.documents.values()
            if _matches_scope(doc.metadata, feature_drop, ancestor)
        ]
        return {"results": matches[:_size(limit, 50)]}

    def search_documents(
        self, query: str, feature_drop: str | None = None, ancestor: str | None = None, limit: int = 8
    ) -> dict:
        self.knowledge.available()
        query = query.strip()
        if not query:
            raise SourceError("invalid_query", "Enter a search query.")
        terms = _tokens(query)
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
        return {"results": [hit for _, hit in hits[:_size(limit, 30)]]}

    def get_document_metadata(self, page_id: str, version: str | int) -> dict:
        return self.knowledge.document(page_id, version).metadata.copy()

    def read_document(
        self, page_id: str, version: str | int, section: str | None = None, offset: int = 0, limit: int = 3500
    ) -> dict:
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
        end = offset + _size(limit, 12_000)
        return {
            "metadata": doc.metadata.copy(), "section": section or "Document",
            "content": content[offset:end], "offset": offset,
            "next_offset": end if end < len(content) else None,
        }

    def _repo(self, repo_id: str) -> Repository:
        repo = self.repositories.get(repo_id)
        if repo is None:
            raise SourceError("repository_not_found", "Repository is not configured.")
        if repo.error:
            raise SourceError("repository_unavailable", repo.error)
        return repo

    def get_repository_metadata(self, repo_id: str) -> dict:
        repo = self._repo(repo_id)
        return repo.metadata(self._indexed_commit(repo_id))

    def search_code(
        self, query: str, repo_ids: list[str] | None = None, path_prefix: str | None = None,
        extensions: list[str] | None = None, limit: int = 30,
    ) -> dict:
        query = query.strip()
        if not query or "\0" in query or "\n" in query or len(query) > 240:
            raise SourceError("invalid_query", "Use a single-line code query up to 240 characters.")
        terms = [query]
        if " " in query:
            terms.append(re.sub(r" +", "_", query))
        allowed = CODE_EXTENSIONS if extensions is None else {
            "." + ext.lower().lstrip(".") for ext in extensions
        } & CODE_EXTENSIONS
        if not allowed:
            raise SourceError("invalid_extensions", "No requested extension is in the readable source list.")
        prefix = _safe_relative(path_prefix) if path_prefix else ""
        repo_ids = list(self.repositories) if repo_ids is None else repo_ids
        if not repo_ids:
            raise SourceError("repository_unavailable", "No repositories are configured for search.")
        limit = _size(limit, 100)
        per_repo = []
        unavailable = []
        for repo_id in dict.fromkeys(repo_ids):
            try:
                per_repo.append(self._repo(repo_id).search(terms, prefix, allowed, limit))
            except SourceError as error:
                unavailable.append({"repo_id": repo_id, "code": error.code, "message": str(error)})
        if unavailable and len(unavailable) == len(set(repo_ids)):
            first = unavailable[0]
            raise SourceError(first["code"], first["message"])
        # Interleave matches so a busy first repository cannot hide the second.
        results = []
        for index in range(limit):
            results.extend(rows[index] for rows in per_repo if index < len(rows))
            if len(results) >= limit:
                break
        return {"results": results[:limit], "unavailable": unavailable}

    def read_file(
        self, repo_id: str, path: str, start_line: int = 1, end_line: int | None = None
    ) -> dict:
        repo = self._repo(repo_id)
        path = _safe_relative(path)
        content = repo.read(path).splitlines()
        start = max(start_line, 1)
        if start > max(1, len(content)) or (end_line is not None and end_line < start):
            raise SourceError("invalid_line_range", "Line range is outside the source.")
        end = min(end_line if end_line is not None else start + 119, start + 119, len(content))
        return {
            "repo_id": repo_id, "branch": repo.branch, "commit_sha": repo.commit_sha, "path": path,
            "start_line": start, "end_line": end,
            "lines": [{"line": index, "text": content[index - 1][:800]} for index in range(start, end + 1)],
            "next_start_line": end + 1 if end < len(content) else None,
        }

    def codegraph_explore(self, repo_id: str, query: str, limit: int = 6) -> dict:
        repo = self._repo(repo_id)
        indexed_commit = self._indexed_commit(repo_id)
        if not indexed_commit:
            raise SourceError("codegraph_unavailable", "No CodeGraph adapter or index is available for this repository.")
        if indexed_commit != repo.commit_sha:
            raise SourceError("codegraph_stale", "CodeGraph index does not match the pinned commit.")
        if not query.strip():
            raise SourceError("invalid_query", "Enter a CodeGraph investigation query.")
        try:
            results = self.codegraph.explore(repo_id=repo_id, commit_sha=repo.commit_sha, query=query)
        except SourceError:
            raise
        except Exception:
            raise SourceError("codegraph_failed", "CodeGraph adapter could not complete the investigation.") from None
        result_id = uuid4().hex
        self.graph_results[result_id] = (repo_id, repo.commit_sha, results[:100])
        return self.codegraph_read_result(result_id, offset=0, limit=limit)

    def codegraph_read_result(self, result_id: str, offset: int = 0, limit: int = 6) -> dict:
        if result_id not in self.graph_results:
            raise SourceError("graph_result_not_found", "This result ID does not belong to the analysis run.")
        repo_id, commit_sha, results = self.graph_results[result_id]
        start = max(0, offset)
        end = start + _size(limit, 30)
        return {
            "result_id": result_id, "repo_id": repo_id, "commit_sha": commit_sha,
            "results": results[start:end], "offset": start,
            "next_offset": end if end < len(results) else None,
        }
