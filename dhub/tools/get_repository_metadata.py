"""Get the pinned repository and CodeGraph metadata."""

TRACE_FIELDS = ("repo_id",)


def get_repository_metadata(self, repo_id: str) -> dict:
    """Get actual pinned branch, commit and CodeGraph coverage for a repository."""
    return self._metadata(self._repo(repo_id))
