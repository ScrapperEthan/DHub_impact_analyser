"""Find file names in a pinned commit."""

from ..repositories import CODE_EXTENSIONS, parse_glob
from .utils.paging import bounded_size, catalogue_page
from .utils.source import relative_path


TRACE_FIELDS = ("repo_id", "path_prefix", "glob", "offset", "limit")


def list_files(
    self, repo_id: str, path_prefix: str | None = None, glob: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict:
    """Discover source, configuration and test file names in a pinned repository.

    Filters use repository-relative paths and a simple glob such as **/tests/*.py.
    Use next_offset to fetch the next page.
    """
    repo = self._repo(repo_id)
    prefix = relative_path(path_prefix) if path_prefix else ""
    paths = repo.matching_paths(prefix, parse_glob(glob), CODE_EXTENSIONS)
    return {
        "repo_id": repo_id, "commit_sha": repo.commit_sha,
        **catalogue_page([{"path": path} for path in paths], offset, bounded_size(limit, 100)),
    }
