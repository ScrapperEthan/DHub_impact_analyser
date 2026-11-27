"""File discovery, search and reads against pinned Git repositories."""

import hashlib
import logging
import os
import re
import subprocess
from concurrent.futures import Future
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from threading import Thread
from time import monotonic

from .utils import SourceError, describe_error, match_excerpt, path_in_source, read_source_text, relative_path

log = logging.getLogger(__name__)
CODE_EXTENSIONS = frozenset({
    ".py", ".pyi", ".sh", ".ps1", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql", ".md", ".txt",
})
MAX_CODE_BYTES = 512_000
MAX_GREP_BYTES = 2_000_000
GREP_PATH_ARGUMENT_BUDGET = 12_000


def _git_invocation(root: Path, args: tuple[str, ...]) -> tuple[list[str], dict[str, str]]:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *args], env


def _git(root: Path, *args: str, timeout: float = 20) -> bytes:
    command, env = _git_invocation(root, args)
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired:
        raise SourceError("source_timeout", "The read-only Git operation timed out.") from None
    except OSError as error:
        raise SourceError(
            "repository_unavailable", f"Git is not available for the repository ({describe_error(error)}).",
        ) from None
    if result.returncode == 0:
        return result.stdout
    message = "The requested Git object or configured revision is unavailable."
    if reason := _git_reason(result.stderr):
        message += f" Git said: {reason}"
    raise SourceError("git_read_failed", message)


def _git_reason(stderr: bytes) -> str:
    # Keep Git's own diagnosis, such as dubious ownership or an unknown revision.
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    reason = next((line for line in lines if line.startswith(("fatal:", "error:"))), lines[0] if lines else "")
    return describe_error(reason) if reason else ""


def _bounded_grep(root: Path, args: tuple[str, ...], limit: int, deadline: float) -> tuple[bytes, bool]:
    if deadline <= monotonic():
        raise SourceError("source_timeout", "Git search reached its time limit.")
    command, env = _git_invocation(root, args)
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as process:
            collected: Future[bytes] = Future()

            def read_output() -> None:
                try:
                    collected.set_result(process.stdout.read(limit + 1))
                except OSError as error:
                    collected.set_exception(error)

            reader = Thread(target=read_output, daemon=True)
            reader.start()
            try:
                output = collected.result(timeout=max(0, deadline - monotonic()))
                if len(output) > limit:
                    return output[:limit], True
                if process.wait(timeout=max(0, deadline - monotonic())) not in (0, 1):
                    raise SourceError("git_read_failed", "Git could not search the pinned commit.")
                return output, False
            finally:
                if process.poll() is None:
                    process.kill()
                reader.join()
    except (TimeoutError, subprocess.TimeoutExpired):
        raise SourceError("source_timeout", "Git search reached its time limit.") from None
    except OSError:
        raise SourceError("repository_unavailable", "Git could not run the repository search.") from None


def _path_batches(paths: list[str]):
    # Leave room below the Windows command-line limit for the root, patterns and quoting.
    batch = []
    size = 0
    for path in paths:
        argument_size = 2 * len(path) + 3
        if batch and size + argument_size > GREP_PATH_ARGUMENT_BUDGET:
            yield batch
            batch, size = [], 0
        batch.append(path)
        size += argument_size
    if batch:
        yield batch


def parse_glob(glob: str | None) -> tuple[str, ...]:
    if glob is None:
        return ()
    if len(glob) > 240:
        raise SourceError("invalid_glob", "Use a repository-relative glob up to 240 characters.")
    parts = PurePosixPath(relative_path(glob)).parts
    if parts.count("**") > 1 or any("**" in part and part != "**" for part in parts):
        raise SourceError("invalid_glob", "Use at most one standalone ** path component.")
    return parts


def _matches_glob(path: str, pattern: tuple[str, ...]) -> bool:
    if not pattern:
        return True
    parts = path.split("/")
    if "**" not in pattern:
        return len(parts) == len(pattern) and all(fnmatchcase(part, glob) for part, glob in zip(parts, pattern))
    pivot = pattern.index("**")
    before, after = pattern[:pivot], pattern[pivot + 1:]
    return (
        len(parts) >= len(pattern) - 1
        and all(fnmatchcase(part, glob) for part, glob in zip(parts, before))
        and all(fnmatchcase(part, glob) for part, glob in zip(reversed(parts), reversed(after)))
    )


def code_lines(content: str) -> list[str]:
    # Git grep numbers LF-delimited lines. Preserve other characters inside the original line.
    lines = content.split("\n")
    return [line.removesuffix("\r") for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])


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
    deadline: float | None = None

    def _timeout(self) -> float:
        timeout = 20 if self.deadline is None else min(20, self.deadline - monotonic())
        if timeout <= 0:
            raise SourceError("source_timeout", "The run deadline has been reached.")
        return timeout

    def _git(self, *args: str) -> bytes:
        return _git(self.root, *args, timeout=self._timeout())

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
            git_root = Path(self._git("rev-parse", "--show-toplevel").decode("utf-8").strip())
            if git_root.resolve() != self.root.resolve():
                raise SourceError("repository_unavailable", "Configure the root of the Git checkout.")
            self.commit_sha = self._git(
                "rev-parse", "--verify", "--end-of-options", f"{self.ref}^{{commit}}"
            ).decode("ascii").strip()
            try:
                branch = self._git(
                    "rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", self.ref
                ).decode("utf-8").strip()
                if branch.startswith("refs/heads/"):
                    self.branch = branch.removeprefix("refs/heads/")
                elif branch.startswith("refs/remotes/"):
                    self.branch = branch.removeprefix("refs/remotes/")
            except (SourceError, ValueError):
                pass
            tree = self._git("ls-tree", "-r", "-l", "-z", "--full-tree", self.commit_sha)
            for entry in tree.split(b"\0"):
                if not entry:
                    continue
                attrs, raw_path = entry.split(b"\t", 1)
                mode, kind, oid, length = attrs.decode("ascii").split()
                try:
                    path = raw_path.decode("utf-8")
                except UnicodeError:
                    continue
                if mode in {"100644", "100755"} and kind == "blob" and _code_path(path):
                    self.files[path] = (oid, int(length))
        except (OSError, ValueError, SourceError) as error:
            self.commit_sha = None
            self.error = (
                str(error) if isinstance(error, SourceError)
                else f"The repository snapshot could not be read ({describe_error(error)})."
            )
            log.warning("Repository %s is unavailable: %s", self.repo_id, self.error)

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
        blob = self._git("cat-file", "blob", oid)
        try:
            content = blob.decode("utf-8-sig")
        except UnicodeError:
            raise SourceError("unsupported_encoding", "This source is not UTF-8 text.") from None
        if "\0" in content:
            raise SourceError("unsupported_encoding", "This source is not UTF-8 text.")
        return content

    def matching_paths(self, prefix: str, pattern: tuple[str, ...], extensions: set[str] | frozenset[str]) -> list[str]:
        return [
            path for path in sorted(self.files)
            if (not prefix or path == prefix or path.startswith(prefix + "/"))
            and PurePosixPath(path).suffix.lower() in extensions
            and _matches_glob(path, pattern)
        ]

    def search(self, queries: list[str], paths: list[str], limit: int) -> tuple[list[dict], str | None]:
        if not paths:
            return [], None
        pattern = re.compile("|".join(re.escape(term) for term in sorted(queries, key=len, reverse=True)), re.IGNORECASE)
        matches = []
        if self.synthetic:
            rows = (
                (path, line, text)
                for path in paths
                for line, text in enumerate(code_lines(self.files[path]), 1)
            )
            self._collect_matches(rows, pattern, matches, limit)
            return matches, None

        args = tuple(part for query in queries for part in ("-e", query))
        # The pinned tree gives the intersection of prefix, glob and extension filters up front.
        command = (
            "--literal-pathspecs", "grep", "--no-textconv", "--no-recurse-submodules",
            "-I", "-i", "-F", "-H", "-n", "-z", "--full-name", "--no-column", "--no-heading",
            "--no-break", "--color=never", f"--max-count={limit}", *args, self.commit_sha, "--",
        )
        deadline = monotonic() + self._timeout()
        budget = MAX_GREP_BYTES
        completed_batch = False
        for batch in _path_batches(paths):
            try:
                output, capped = _bounded_grep(self.root, (*command, *batch), budget, deadline)
            except SourceError as error:
                if not completed_batch:
                    raise
                return matches, str(error)
            self._collect_matches(self._grep_rows(output), pattern, matches, limit)
            if capped:
                return matches, "Git search reached its output byte limit. Narrow the path or query."
            if len(matches) == limit:
                return matches, None
            budget -= len(output)
            completed_batch = True
        return matches, None

    def _collect_matches(self, rows, pattern: re.Pattern, matches: list[dict], limit: int) -> None:
        for path, line, text in rows:
            match = pattern.search(text)
            if not match:
                continue
            matches.append({
                "repo_id": self.repo_id, "branch": self.branch, "commit_sha": self.commit_sha,
                "path": path, "line": line, "column": match.start() + 1, "matched_term": match[0],
                "text": match_excerpt(text, match.start(), match.end(), 400),
            })
            if len(matches) == limit:
                return

    def _grep_rows(self, output: bytes):
        tree_prefix = f"{self.commit_sha}:".encode("ascii")
        # Ignore the unfinished last row when the byte budget cuts through one.
        for row in output.split(b"\n")[:-1]:
            parts = row.split(b"\0", 2)
            if len(parts) != 3:
                continue
            name, line, text = parts
            if not name.startswith(tree_prefix) or not line.isdigit():
                continue
            path = name[len(tree_prefix):].decode("utf-8")
            if path not in self.files:
                continue
            try:
                line_number = int(line)
                source = text.decode("utf-8-sig" if line_number == 1 else "utf-8").removesuffix("\r")
            except UnicodeError:
                continue
            yield path, line_number, source


def _code_path(path: str) -> bool:
    try:
        parts = PurePosixPath(relative_path(path)).parts
    except SourceError:
        return False
    return all(part.casefold() != ".git" for part in parts) and PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS
