"""Prepare and describe the document and code sources for one analysis run."""

from __future__ import annotations

from pathlib import Path

from .codegraph import BoundCodeGraphAdapter
from .documents import Knowledge
from .repositories import DEMO_ROOT, DemoCodeGraph, Repository
from .repositories import CodeGraphAdapter as CodeGraphAdapter
from .tools.code import CodeTools
from .tools.domain import DomainTools
from .tools.utils.source import SourceError as SourceError


class Sources:
    """Construct one instance per analysis run to isolate snapshots and graph results."""

    def __init__(
        self,
        *,
        data_mode: str = "demo",
        manifest_path: str | Path | None = None,
        repositories: list[dict] | None = None,
        codegraph: CodeGraphAdapter | BoundCodeGraphAdapter | None = None,
        deadline: float | None = None,
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
        self.repositories: dict[str, Repository] = {}
        for entry in repositories or []:
            repo = Repository(
                repo_id=entry["repo_id"], root=Path(entry["path"]).resolve(),
                ref=entry.get("ref") or "HEAD", synthetic=data_mode == "demo", deadline=deadline,
            )
            if repo.repo_id in self.repositories:
                raise ValueError("Repository IDs must be unique")
            repo.pin()
            self.repositories[repo.repo_id] = repo
        self.codegraph = DemoCodeGraph(self.repositories) if data_mode == "demo" else codegraph
        self.domain = DomainTools(self.knowledge)
        self.code = CodeTools(self.repositories, self.codegraph)
        self.graph_results = self.code.graph_results

        # Direct source calls use the same methods as the agent toolsets.
        self.list_documents = self.domain.list_documents
        self.search_documents = self.domain.search_documents
        self.get_document_metadata = self.domain.get_document_metadata
        self.read_document = self.domain.read_document
        self.get_repository_metadata = self.code.get_repository_metadata
        self.search_code = self.code.search_code
        self.list_files = self.code.list_files
        self.read_file = self.code.read_file
        self.codegraph_explore = self.code.codegraph_explore
        self.codegraph_read_result = self.code.codegraph_read_result

    def snapshot(self) -> dict:
        return {
            "knowledge_snapshot_id": self.knowledge.snapshot_id,
            "knowledge": self.knowledge.snapshot(),
            "repositories": self.code.repository_snapshots(),
        }
