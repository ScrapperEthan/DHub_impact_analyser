"""Search code, configuration and tests in pinned repositories."""

import re
from typing import Literal

from ..sources.repositories import CODE_EXTENSIONS, parse_glob
from ..sources.utils import SourceError, relative_path
from .utils.paging import bounded_size, search_page


TRACE_FIELDS = ("query", "queries", "repo_ids", "path_prefix", "extensions", "glob", "mode", "limit")
MAX_CODE_QUERIES = 8


def search_code(
    self, query: str | None = None, repo_ids: list[str] | None = None, path_prefix: str | None = None,
    extensions: list[str] | None = None, limit: int = 30, glob: str | None = None,
    mode: Literal["literal", "identifier"] = "literal",
    queries: list[str] | None = None,
) -> dict:
    """Search pinned repositories within optional path, glob and extension filters.

    Pass query or queries. A line may match any of up to eight queries and is returned once.
    literal mode matches text case-insensitively. identifier mode also tries snake_case
    and camel/Pascal case forms. Read matching source lines before citing them.
    """
    requested = ([query] if query is not None else []) + (queries or [])
    if not 1 <= len(requested) <= MAX_CODE_QUERIES:
        raise SourceError("invalid_query", "Supply one to eight code queries.")
    if any(
        not term.strip() or len(term.strip()) > 240 or any(char in term for char in "\0\r\n")
        for term in requested
    ):
        raise SourceError("invalid_query", "Use non-empty single-line code queries up to 240 characters each.")
    if mode not in {"literal", "identifier"}:
        raise SourceError("invalid_search_mode", "Use literal or identifier search mode.")
    original_terms = list(dict.fromkeys(term.strip() for term in requested))
    terms = original_terms if mode == "literal" else list(dict.fromkeys(
        variant for term in original_terms for variant in _identifier_terms(term)
    ))
    allowed = CODE_EXTENSIONS if extensions is None else {
        "." + ext.lower().lstrip(".") for ext in extensions
    } & CODE_EXTENSIONS
    if not allowed:
        raise SourceError("invalid_extensions", "No requested extension is in the readable source list.")
    prefix = relative_path(path_prefix) if path_prefix else ""
    pattern = parse_glob(glob)
    repo_ids = list(dict.fromkeys(self.repositories if repo_ids is None else repo_ids))
    limit = bounded_size(limit, 100)
    scope = {
        "repo_ids": repo_ids, "path_prefix": prefix or None, "extensions": sorted(allowed),
        "glob": "/".join(pattern) or None, "mode": mode, "limit": limit,
        "queries": original_terms,
    }
    per_repo = []
    unavailable = []
    warnings = []
    for repo_id in repo_ids:
        try:
            repo = self._repo(repo_id)
            paths = repo.matching_paths(prefix, pattern, allowed)
            matches, warning = repo.search(terms, paths, limit + 1)
        except SourceError as error:
            unavailable.append({"repo_id": repo_id, "code": error.code, "message": str(error)})
            warnings.append(f"{repo_id}: {error}")
        else:
            per_repo.append(matches)
            if warning:
                warnings.append(f"{repo_id}: {warning}")
    if not repo_ids:
        warnings.append("No repositories are configured or selected for search.")
    status = "complete"
    if not per_repo:
        status = "unavailable"
    elif warnings:
        status = "partial"
    # Interleave matches so a busy first repository cannot hide the second.
    results = []
    for index in range(limit + 1):
        results.extend(rows[index] for rows in per_repo if index < len(rows))
        if len(results) > limit:
            break
    return search_page(results, limit, status=status, scope=scope, warnings=warnings, unavailable=unavailable)


def _identifier_terms(query: str) -> list[str]:
    separated = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", query)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", separated)
    words = re.findall(r"[^\W_]+", separated.lower())
    if len(words) < 2:
        return [query]
    camel = words[0] + "".join(word.title() for word in words[1:])
    # The case-insensitive search already covers PascalCase and upper snake case.
    return list(dict.fromkeys([query, "_".join(words), camel]))
