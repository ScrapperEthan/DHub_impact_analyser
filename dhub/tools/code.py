"""Bind the Git and CodeGraph tools to a run's repositories."""

from ..codegraph import BoundCodeGraphAdapter, CodeGraphAdapter, CodeGraphSession
from ..repositories import Repository
from . import codegraph_explore as explore_tool
from . import codegraph_read_result as graph_read_tool
from . import get_repository_metadata as metadata_tool
from . import list_files as list_tool
from . import read_file as read_tool
from . import search_code as search_tool
from .read_file import MAX_CODE_READ_CHARS as MAX_CODE_READ_CHARS
from .registry import ToolDefinition, build_toolset, define_tool
from .utils.source import SourceError


class CodeTools:
    def __init__(
        self, repositories: dict[str, Repository], codegraph: CodeGraphAdapter | BoundCodeGraphAdapter | None,
    ):
        self.repositories = repositories
        self.graph = CodeGraphSession(codegraph, repositories)
        self.graph_results = self.graph.results

    get_repository_metadata = metadata_tool.get_repository_metadata
    search_code = search_tool.search_code
    list_files = list_tool.list_files
    read_file = read_tool.read_file
    codegraph_explore = explore_tool.codegraph_explore
    codegraph_read_result = graph_read_tool.codegraph_read_result

    def toolset(self) -> dict[str, ToolDefinition]:
        return build_toolset(
            define_tool(self.get_repository_metadata, trace_fields=metadata_tool.TRACE_FIELDS),
            define_tool(self.search_code, trace_fields=search_tool.TRACE_FIELDS),
            define_tool(self.list_files, trace_fields=list_tool.TRACE_FIELDS),
            define_tool(
                self.read_file, trace_fields=read_tool.TRACE_FIELDS, evidence_factory=read_tool.code_evidence,
            ),
            define_tool(
                self.codegraph_explore, args_schema=explore_tool.GraphExploreArgs, trace_fields=explore_tool.TRACE_FIELDS,
            ),
            define_tool(
                self.codegraph_read_result, args_schema=graph_read_tool.GraphReadArgs,
                trace_fields=graph_read_tool.TRACE_FIELDS,
            ),
        )

    def repository_snapshots(self) -> list[dict]:
        return [self._metadata(repo) for repo in self.repositories.values()]

    def _metadata(self, repo: Repository) -> dict:
        graph = self.graph.metadata(repo)
        return {**repo.metadata(graph["codegraph_commit_sha"]), **graph}

    def _repo(self, repo_id: str) -> Repository:
        repo = self.repositories.get(repo_id)
        if repo is None:
            raise SourceError("repository_not_found", "Repository is not configured.")
        if repo.error:
            raise SourceError("repository_unavailable", repo.error)
        return repo
