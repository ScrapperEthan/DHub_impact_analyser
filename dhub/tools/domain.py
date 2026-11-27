"""Bind the document tools to a run's knowledge snapshot."""

from ..sources.documents import Knowledge
from . import get_document_metadata as metadata_tool
from . import list_documents as list_tool
from . import read_document as read_tool
from . import search_documents as search_tool
from .registry import ToolDefinition, build_toolset, define_tool


class DomainTools:
    def __init__(self, knowledge: Knowledge):
        self.knowledge = knowledge

    search_documents = search_tool.search_documents
    list_documents = list_tool.list_documents
    read_document = read_tool.read_document
    get_document_metadata = metadata_tool.get_document_metadata

    def toolset(self) -> dict[str, ToolDefinition]:
        return build_toolset(
            define_tool(self.search_documents, trace_fields=search_tool.TRACE_FIELDS),
            define_tool(self.list_documents, trace_fields=list_tool.TRACE_FIELDS),
            define_tool(
                self.read_document, trace_fields=read_tool.TRACE_FIELDS, evidence_factory=read_tool.document_evidence,
            ),
            define_tool(self.get_document_metadata, trace_fields=metadata_tool.TRACE_FIELDS),
        )
