"""Per-run CodeGraph binding, result capture and continuation."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from time import monotonic
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from .tools.utils.source import SourceError

if TYPE_CHECKING:
    from .repositories import Repository


MAX_GRAPH_RELATIONS = 100
MAX_GRAPH_CAPTURE_CHARS = 24_000
MAX_GRAPH_READ_CHARS = 12_000
DEFAULT_GRAPH_READ_CHARS = 3_500
GRAPH_TIMEOUT = 20


class CodeGraphAdapter(Protocol):
    """Legacy provider that exposes a Git commit and relation rows."""

    def indexed_commit(self, repo_id: str) -> str | None: ...

    def explore(self, *, repo_id: str, commit_sha: str, query: str) -> list[dict]: ...


@dataclass(frozen=True)
class CodeGraphIndex:
    provider: str
    commit_sha: str
    index_id: str | None
    indexed_languages: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CodeGraphResult:
    index: CodeGraphIndex
    results: list[dict] | None = None
    content: str | None = None
    status: Literal["complete", "partial", "unavailable"] = "complete"
    truncated: bool = False
    warnings: tuple[str, ...] = ()


class BoundCodeGraphAdapter(Protocol):
    """Provider for a named index. Deadlines are absolute monotonic times."""

    def index_info(self, repo_id: str, *, deadline: float) -> CodeGraphIndex | None: ...

    def explore(
        self, *, repo_id: str, index: CodeGraphIndex, query: str, deadline: float,
    ) -> CodeGraphResult: ...


class UnavailableCodeGraphAdapter:
    """Represent a configured adapter that failed to load."""

    def indexed_commit(self, repo_id: str) -> str | None:
        raise RuntimeError("The configured CodeGraph adapter could not be loaded.")


def _deadline(repo: Repository) -> float:
    limit = monotonic() + GRAPH_TIMEOUT
    return min(repo.deadline, limit) if repo.deadline is not None else limit


def _check_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise SourceError("codegraph_timeout", "CodeGraph reached its time limit. Continue with Git search and source reads.")


def _capture(result: CodeGraphResult, deadline: float) -> CodeGraphResult:
    if (
        result.status not in ("complete", "partial", "unavailable")
        or not isinstance(result.truncated, bool)
        or not isinstance(result.warnings, (list, tuple))
        or any(not isinstance(warning, str) for warning in result.warnings[:8])
        or (result.content is not None and (not isinstance(result.content, str) or result.results is not None))
        or (result.results is not None and not isinstance(result.results, list))
        or (result.status != "unavailable" and result.results is None and result.content is None)
    ):
        raise SourceError("codegraph_invalid_result", "CodeGraph returned an invalid result. Continue with source reads.")
    warnings = tuple(warning[:240] for warning in result.warnings[:8])
    if result.status == "unavailable":
        return replace(result, results=[] if result.content is None else None,
                       content="" if result.content is not None else None, warnings=warnings)
    content = result.content
    rows = None
    if content is not None:
        content = content[:MAX_GRAPH_CAPTURE_CHARS]
        capped = len(result.content) > len(content)
    else:
        provided = result.results or []
        rows = []
        remaining = MAX_GRAPH_CAPTURE_CHARS
        for row in provided[:MAX_GRAPH_RELATIONS]:
            _check_deadline(deadline)
            if not isinstance(row, dict):
                raise SourceError("codegraph_invalid_result", "CodeGraph returned a relation without fields.")
            try:
                encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                raise SourceError("codegraph_invalid_result", "CodeGraph returned invalid relation fields.") from None
            if len(encoded) > remaining:
                break
            # Capture an independent JSON value in case a provider reuses its result objects.
            rows.append(json.loads(encoded))
            remaining -= len(encoded)
        capped = len(provided) > len(rows)
    if capped:
        warnings += ("CodeGraph output exceeded the capture limit. Narrow the query for remaining results.",)
    truncated = capped or result.truncated
    return replace(
        result, results=rows, content=content, warnings=warnings, truncated=truncated,
        status="partial" if truncated else result.status,
    )


class CodeGraphSession:
    def __init__(
        self, adapter: CodeGraphAdapter | BoundCodeGraphAdapter | None, repositories: dict[str, Repository],
    ):
        self.adapter = adapter
        self.bound = callable(getattr(adapter, "index_info", None))
        self.indexes: dict[str, CodeGraphIndex] = {}
        self.errors: dict[str, SourceError] = {}
        self.results: dict[str, tuple[str, CodeGraphResult]] = {}
        for repo in repositories.values():
            if repo.error:
                continue
            try:
                self.indexes[repo.repo_id] = self._current_index(repo.repo_id, _deadline(repo))
            except SourceError as error:
                self.errors[repo.repo_id] = error

    def _current_index(self, repo_id: str, deadline: float) -> CodeGraphIndex:
        _check_deadline(deadline)
        try:
            if self.adapter is None:
                index = None
            elif self.bound:
                index = self.adapter.index_info(repo_id, deadline=deadline)
            else:
                sha = self.adapter.indexed_commit(repo_id)
                index = CodeGraphIndex("legacy", sha, None) if sha else None
        except TimeoutError:
            raise SourceError("codegraph_timeout", "CodeGraph index lookup timed out.") from None
        except Exception:
            raise SourceError("codegraph_index_unavailable", "CodeGraph could not read index information.") from None
        _check_deadline(deadline)
        if index is None:
            raise SourceError("codegraph_unavailable", "No CodeGraph index is available. Use Git search and source reads.")
        if (
            not isinstance(index, CodeGraphIndex)
            or not isinstance(index.commit_sha, str) or not index.commit_sha
            or not isinstance(index.provider, str) or not index.provider
            or (self.bound and (not isinstance(index.index_id, str) or not index.index_id))
        ):
            raise SourceError("codegraph_invalid_index", "The provider must identify its pinned CodeGraph index.")
        return index

    def metadata(self, repo: Repository) -> dict:
        index = self.indexes.get(repo.repo_id)
        if index is None:
            why = self.errors.get(repo.repo_id)
            return {
                "codegraph_commit_sha": None,
                "codegraph_status": "unavailable",
                "codegraph_reason": why.code if why else "repository_unavailable",
            }
        matches = index.commit_sha == repo.commit_sha
        return {
            "codegraph_commit_sha": index.commit_sha,
            "codegraph_index_id": index.index_id,
            "codegraph_provider": index.provider,
            "codegraph_indexed_languages": index.indexed_languages,
            "codegraph_status": "available" if matches else "stale",
            **({} if matches else {"codegraph_reason": "codegraph_stale"}),
        }

    def _verify_index(self, repo_id: str, pinned: CodeGraphIndex, deadline: float) -> None:
        if self._current_index(repo_id, deadline) != pinned:
            raise SourceError(
                "codegraph_index_changed", "The CodeGraph index changed during this run. Continue with Git source investigation.",
            )

    def explore(self, repo: Repository, query: str, limit: int, max_chars: int) -> dict:
        index = self.indexes.get(repo.repo_id)
        if index is None:
            raise self.errors[repo.repo_id]
        if index.commit_sha != repo.commit_sha:
            raise SourceError("codegraph_stale", "The CodeGraph index does not match the pinned commit. Use Git tools.")
        deadline = _deadline(repo)
        self._verify_index(repo.repo_id, index, deadline)
        try:
            if self.bound:
                result = self.adapter.explore(repo_id=repo.repo_id, index=index, query=query, deadline=deadline)
            else:
                rows = self.adapter.explore(repo_id=repo.repo_id, commit_sha=index.commit_sha, query=query)
                result = CodeGraphResult(index, results=rows)
        except TimeoutError:
            raise SourceError("codegraph_timeout", "CodeGraph query timed out. Use Git search and source reads.") from None
        except Exception:
            raise SourceError("codegraph_failed", "CodeGraph could not complete the query. Use Git tools.") from None
        _check_deadline(deadline)
        self._verify_index(repo.repo_id, index, deadline)
        if not isinstance(result, CodeGraphResult):
            raise SourceError("codegraph_invalid_result", "The provider must return a CodeGraphResult.")
        if result.index != index:
            raise SourceError("codegraph_index_changed", "CodeGraph returned results for a different index. Use Git tools.")
        captured = _capture(result, deadline)
        _check_deadline(deadline)
        result_id = None
        if captured.status != "unavailable":
            result_id = uuid4().hex
            self.results[result_id] = (repo.repo_id, captured)
        return self._page(repo.repo_id, result_id, captured, 0, limit, max_chars)

    def read_result(self, result_id: str, offset: int, limit: int, max_chars: int) -> dict:
        if result_id not in self.results:
            raise SourceError("graph_result_not_found", "Use a result_id returned by CodeGraph in this run.")
        repo_id, result = self.results[result_id]
        return self._page(repo_id, result_id, result, offset, limit, max_chars)

    @staticmethod
    def _page(
        repo_id: str, result_id: str | None, result: CodeGraphResult, offset: int, limit: int, max_chars: int,
    ) -> dict:
        if result.content is not None:
            end = offset + max_chars
            content = result.content[offset:end]
            window = {"content": content, "result_format": "text", "returned": len(content)}
            captured_count = len(result.content)
        else:
            end = offset + limit
            rows = result.results or []
            contents = deepcopy(rows[offset:end])
            window = {"results": contents, "result_format": "relations", "returned": len(contents)}
            captured_count = len(rows)
        if offset > captured_count:
            raise SourceError("invalid_graph_window", "Use a previous next_offset within the captured result.")
        return {
            "repo_id": repo_id, "result_id": result_id, "commit_sha": result.index.commit_sha,
            "provider": result.index.provider, "index_id": result.index.index_id,
            "status": result.status, "warnings": list(result.warnings), "truncated": result.truncated,
            "captured_count": captured_count, "offset": offset,
            "has_more": end < captured_count, "next_offset": end if end < captured_count else None,
            **window,
        }
