"""Git and CodeGraph tools exposed to the Code Investigator."""

import re
from uuid import uuid4

from ..repositories import CODE_EXTENSIONS, CodeGraphAdapter, Repository
from ..source_utils import SourceError, bounded_size, relative_path
from .registry import ToolDefinition, build_toolset, define_tool


class CodeTools:
    def __init__(self, repositories: dict[str, Repository], codegraph: CodeGraphAdapter | None):
        self.repositories = repositories
        self.codegraph = codegraph
        self.graph_results: dict[str, tuple[str, str, list[dict]]] = {}

    def toolset(self) -> dict[str, ToolDefinition]:
        return build_toolset(
            define_tool(self.get_repository_metadata, trace_fields=("repo_id",)),
            define_tool(
                self.search_code,
                trace_fields=("query", "repo_ids", "path_prefix", "extensions", "limit"),
            ),
            define_tool(
                self.read_file,
                trace_fields=("repo_id", "path", "start_line", "end_line"),
                evidence_factory=_code_evidence,
            ),
            define_tool(self.codegraph_explore, trace_fields=("repo_id", "query", "limit")),
            define_tool(self.codegraph_read_result, trace_fields=("result_id", "offset", "limit")),
        )

    def repository_snapshots(self) -> list[dict]:
        return [repo.metadata(self._indexed_commit(repo.repo_id)) for repo in self.repositories.values()]

    def _indexed_commit(self, repo_id: str) -> str | None:
        if self.codegraph is None:
            return None
        try:
            return self.codegraph.indexed_commit(repo_id)
        except Exception:
            return None

    def _repo(self, repo_id: str) -> Repository:
        repo = self.repositories.get(repo_id)
        if repo is None:
            raise SourceError("repository_not_found", "Repository is not configured.")
        if repo.error:
            raise SourceError("repository_unavailable", repo.error)
        return repo

    def get_repository_metadata(self, repo_id: str) -> dict:
        """Get actual pinned branch, commit and CodeGraph coverage for a repository."""
        repo = self._repo(repo_id)
        return repo.metadata(self._indexed_commit(repo_id))

    def search_code(
        self, query: str, repo_ids: list[str] | None = None, path_prefix: str | None = None,
        extensions: list[str] | None = None, limit: int = 30,
    ) -> dict:
        """Find literal terms across configured repositories. A match is a discovery hint."""
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
        prefix = relative_path(path_prefix) if path_prefix else ""
        repo_ids = list(self.repositories) if repo_ids is None else repo_ids
        if not repo_ids:
            raise SourceError("repository_unavailable", "No repositories are configured for search.")
        limit = bounded_size(limit, 100)
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
        """Read numbered original source lines for a pinned repository. Use its evidence ID."""
        repo = self._repo(repo_id)
        path = relative_path(path)
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
        """Explore static relations for a symbol in an available CodeGraph index."""
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
        """Read the next window of the saved CodeGraph result."""
        if result_id not in self.graph_results:
            raise SourceError("graph_result_not_found", "This result ID does not belong to the analysis run.")
        repo_id, commit_sha, results = self.graph_results[result_id]
        start = max(0, offset)
        end = start + bounded_size(limit, 30)
        return {
            "result_id": result_id, "repo_id": repo_id, "commit_sha": commit_sha,
            "results": results[start:end], "offset": start,
            "next_offset": end if end < len(results) else None,
        }


def _code_evidence(result: dict) -> dict | None:
    if not result.get("lines"):
        return None
    fields = {
        key: result.get(key)
        for key in ("repo_id", "branch", "commit_sha", "path", "start_line", "end_line")
    }
    excerpt = "\n".join(f"{line['line']}  {line['text']}" for line in result["lines"])
    return {**fields, "source_type": "code", "excerpt": excerpt}
