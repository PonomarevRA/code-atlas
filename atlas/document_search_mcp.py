"""Три scoped MCP операции captured search поверх общего loopback HTTP bridge."""
import json
from typing import Annotated
from urllib.parse import quote, urlencode

from mcp.types import ToolAnnotations
from pydantic import Field

DOCUMENT_SEARCH_TOOLS = ['build_document_search', 'get_document_search_index', 'search_captured_documents']


def register_document_search_tools(server, request, selected_project, fixed_project=None, fixed_data_map=None):
    """Фиксирует project scope до HTTP; data-map connection не получает document доступ."""
    read = ToolAnnotations(readOnlyHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

    def project_scope(requested):
        """Bridge требует существующий project и сохраняет ограничения MCP connection."""
        if fixed_data_map:
            raise ValueError('This connection is limited to a data map.')
        if not fixed_project and not requested:
            raise ValueError('A registered projectId is required for captured document search.')
        project = selected_project(requested)
        if not project:
            raise ValueError('A registered projectId is required for captured document search.')
        return project

    def bounded(result):
        """Transport не обрезает hashes/citations или исходный текст отдельного hit."""
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 64000:
            raise ValueError('Captured search response exceeds 64000 bytes; request fewer hits.')
        return result

    @server.tool(annotations=write)
    async def build_document_search(mapId: str, snapshotId: str, codeSnapshotId: str,
                                    projectId: str | None = None, force: bool = False) -> dict:
        """Explicitly build the selected captured Markdown index with a preinstalled local model.

        Pins document and code snapshot IDs. Does not parse documents, read originals,
        download models, rebuild Graphify or replace the existing code documentation corpus.
        Missing local model returns MODEL_UNAVAILABLE.
        """
        project = project_scope(projectId)
        return bounded(await request('POST', '/api/document-maps/' + quote(mapId, safe='') + '/search-index',
            {'projectId': project, 'snapshotId': snapshotId, 'codeSnapshotId': codeSnapshotId, 'force': force}))

    @server.tool(annotations=read)
    async def get_document_search_index(mapId: str, snapshotId: str, codeSnapshotId: str,
                                        projectId: str | None = None) -> dict:
        """Read exact captured-search status without loading a model, parser, or current graph."""
        project = project_scope(projectId)
        query = urlencode({'projectId': project, 'snapshotId': snapshotId, 'codeSnapshotId': codeSnapshotId})
        return bounded(await request('GET', '/api/document-maps/' + quote(mapId, safe='') + '/search-index?' + query))

    @server.tool(annotations=read)
    async def search_captured_documents(mapId: str, snapshotId: str, codeSnapshotId: str, query: str,
                                        projectId: str | None = None,
                                        limit: Annotated[int, Field(ge=1, le=50)] = 8) -> dict:
        """Search an already built captured Markdown index with pinned source provenance.

        Reads no original file and performs no implicit build or model download. Hits
        preserve both snapshot IDs; explicit file links do not claim runtime call edges.
        """
        project = project_scope(projectId)
        return bounded(await request('POST', '/api/document-maps/' + quote(mapId, safe='') + '/search',
            {'projectId': project, 'snapshotId': snapshotId, 'codeSnapshotId': codeSnapshotId,
             'query': query, 'limit': limit}))
