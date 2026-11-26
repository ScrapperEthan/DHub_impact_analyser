"""Pinned Git repositories and CodeGraph adapters."""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from .source_utils import SourceError, path_in_source, query_tokens, read_source_text, relative_path

DEMO_ROOT = Path(__file__).resolve().parent.parent / "demo"
CODE_EXTENSIONS = frozenset({".py", ".pyi", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql", ".md", ".txt"})
MAX_CODE_BYTES = 512_000
MAX_DEMO_GRAPH_BYTES = 8_000_000


class CodeGraphAdapter(Protocol):
    """Bind graph evidence to the Git commit the index represents."""

    def indexed_commit(self, repo_id: str) -> str | None: ...

    def explore(self, *, repo_id: str, commit_sha: str, query: str) -> list[dict]: ...


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
                        content = read_source_text(path_in_source(self.root, relative), MAX_CODE_BYTES)
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
        parts = PurePosixPath(relative_path(path)).parts
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
            self.edges = json.loads(read_source_text(DEMO_ROOT / "codegraph.json", MAX_DEMO_GRAPH_BYTES))
        except (ValueError, OSError, SourceError):
            self.edges = {}

    def indexed_commit(self, repo_id: str) -> str | None:
        return self.repositories[repo_id].commit_sha if repo_id in self.edges else None

    def explore(self, *, repo_id: str, commit_sha: str, query: str) -> list[dict]:
        terms = query_tokens(query)
        matches = []
        for edge in self.edges.get(repo_id, []):
            label = " ".join(str(edge.get(key, "")) for key in ("caller", "callee", "path")).casefold()
            if any(term in label for term in terms):
                matches.append(edge)
        return matches
