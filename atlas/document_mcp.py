"""Общие bounded MCP-запросы документов поверх loopback API, без локальных parsers."""
import json
from typing import Annotated, Literal
from urllib.parse import quote, urlencode

from mcp.types import ToolAnnotations
from pydantic import Field

MAX_BYTES = 64_000
DOCUMENT_TOOLS = ['discover_document_files', 'list_document_maps', 'ensure_document_map',
                  'get_map_job', 'cancel_map_job', 'query_document_tree', 'read_document_table',
                  'read_document_content', 'create_document_view']


def bounded(result):
    """Не обрезает identity/locators; предлагает сузить запрос при большом single item."""
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError('Document response exceeds 64000 bytes; narrow query/range/projection.')
    return result


def register_document_tools(server, request, selected_project, fixed_project=None, fixed_data_map=None):
    """Закрепляет scope до HTTP; клиент не открывает исходники и не создаёт store."""
    read = ToolAnnotations(readOnlyHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

    def project_scope(requested):
        """Fixed data-map connection не получает доступ к независимому document слою."""
        if fixed_data_map:
            raise ValueError('This connection is limited to a data map.')
        return selected_project(requested) if fixed_project or requested else None

    async def selected_map(map_id, project_id=None):
        """Проверяет принадлежность до выдачи/refresh/document job mutation."""
        project_id = project_scope(project_id)
        suffix = '?' + urlencode({'projectId': project_id}) if project_id else ''
        result = await request('GET', '/api/document-maps/' + quote(map_id, safe='') + '/info' + suffix)
        if project_id and result.get('projectId') != project_id:
            raise ValueError('Document map belongs to another project.')
        return project_id

    def job_scope(requested_project, requested_map):
        """Общий job gateway допускает собственную fixed data map, но не чужую."""
        project = selected_project(requested_project) if fixed_project or requested_project else None
        if fixed_data_map:
            if requested_map is not None and requested_map != fixed_data_map:
                raise ValueError('Job belongs to another data map.')
            requested_map = fixed_data_map
        return project, requested_map

    @server.tool(annotations=read)
    async def discover_document_files(projectId: str | None = None, root: str | None = None,
                                      cursor: str | None = None, limit: Annotated[int, Field(ge=1, le=100)] = 100,
                                      query: str = '') -> dict:
        """List local MD/YAML/Helm/Office/PDF/log names/stat; never parse or build."""
        project = project_scope(projectId)
        if project and root is not None:
            raise ValueError('A project-scoped request cannot supply root.')
        body = {'projectId': project} if project else {'root': root}
        return bounded(await request('POST', '/api/document-files', {**body, 'cursor': cursor, 'limit': limit, 'query': query}))

    @server.tool(annotations=read)
    async def list_document_maps(projectId: str | None = None, cursor: str | None = None,
                                 limit: Annotated[int, Field(ge=1, le=100)] = 100) -> dict:
        """Read existing document maps; no discovery or parser invocation."""
        project = project_scope(projectId)
        suffix = '?' + urlencode({key: value for key, value in {'projectId': project, 'cursor': cursor, 'limit': limit}.items() if value is not None})
        return bounded(await request('GET', '/api/document-maps' + suffix))

    @server.tool(annotations=write)
    async def ensure_document_map(files: list[str] | None = None, mapId: str | None = None,
                                   projectId: str | None = None, root: str | None = None,
                                   title: str = 'Документы', strategy: str | None = None,
                                   options: dict | None = None, context: dict | None = None,
                                   force: bool = False, allowPartial: bool = False) -> dict:
        """Explicitly capture/index selected files. Poll get_map_job; reads never rebuild.

        A mapId refreshes its selected files unless files are supplied. Runtime parser
        options and mod fingerprints are pinned. No OCR, macros, formula evaluation,
        template execution, downloads or automatic legacy conversion.
        """
        project = project_scope(projectId)
        if project and root is not None:
            raise ValueError('A project-scoped request cannot supply root.')
        if mapId:
            await selected_map(mapId, project)
        body = {'files': files, 'mapId': mapId, 'projectId': project, 'root': root, 'title': title,
                'strategy': strategy, 'options': options, 'context': context, 'force': force, 'allowPartial': allowPartial}
        return bounded(await request('POST', '/api/document-maps/ensure', body))

    @server.tool(annotations=read)
    async def get_map_job(jobId: str, projectId: str | None = None, mapId: str | None = None) -> dict:
        """Read data/document job state under fixed scope; restart never resumes hidden work."""
        project, mapId = job_scope(projectId, mapId)
        query = {key: value for key, value in {'projectId': project, 'mapId': mapId}.items() if value}
        return bounded(await request('GET', '/api/map-jobs/' + quote(jobId, safe='') + ('?' + urlencode(query) if query else '')))

    @server.tool(annotations=write)
    async def cancel_map_job(jobId: str, projectId: str | None = None, mapId: str | None = None) -> dict:
        """Cancel only an authorized consumer; shared extraction for another job continues."""
        project, mapId = job_scope(projectId, mapId)
        return bounded(await request('POST', '/api/map-jobs/' + quote(jobId, safe='') + '/cancel',
                                     {key: value for key, value in {'projectId': project, 'mapId': mapId}.items() if value}))

    async def query(map_id, snapshot_id, operation, project_id, **values):
        """Точные snapshot/node IDs приходят из предыдущего ответа, не вычисляются клиентом."""
        project = project_scope(project_id)
        body = {'snapshotId': snapshot_id, 'projectId': project, **{key: value for key, value in values.items() if value is not None}}
        return bounded(await request('POST', '/api/document-maps/' + quote(map_id, safe='') + '/query/' + operation, body))

    @server.tool(annotations=read)
    async def query_document_tree(mapId: str, snapshotId: str, projectId: str | None = None,
                                  operation: Literal['tree', 'search', 'node', 'graph'] = 'tree',
                                  documentId: str | None = None, parentId: str | None = None,
                                  nodeId: str | None = None, queryText: str | None = None,
                                  cursor: str | None = None,
                                  limit: Annotated[int, Field(ge=1, le=50)] = 30) -> dict:
        """Read captured roots/children, search, node or bounded graph; no parser/source read.

        Use returned parent/node/document IDs, pinned snapshotId and nextCursor. Coverage
        truncation and response pagination are distinct; inferred structure is not execution.
        For node/graph omit page-only fields. Full text uses read_document_content.
        """
        values = {'documentId': documentId}
        if operation == 'tree':
            values.update(parentId=parentId, cursor=cursor, limit=limit)
        elif operation == 'search':
            values.update(query=queryText or '', cursor=cursor, limit=limit)
        elif operation == 'node':
            if not nodeId:
                raise ValueError('nodeId is required for node.')
            values['nodeId'] = nodeId
        return await query(mapId, snapshotId, operation, projectId, **values)

    @server.tool(annotations=read)
    async def read_document_table(mapId: str, snapshotId: str, nodeId: str, projectId: str | None = None,
                                  documentId: str | None = None, rowStart: int = 0, rowCount: int = 20,
                                  columnStart: int = 0, columnCount: int = 20) -> dict:
        """Read a sparse indexed table window (zero-based). Formula/cache presence/freshness differ."""
        return await query(mapId, snapshotId, 'table', projectId, nodeId=nodeId, documentId=documentId,
                           rowStart=rowStart, rowCount=rowCount, columnStart=columnStart, columnCount=columnCount)

    @server.tool(annotations=read)
    async def read_document_content(mapId: str, snapshotId: str, nodeId: str, projectId: str | None = None,
                                    documentId: str | None = None, offset: int = 0, limit: int = 4000) -> dict:
        """Read captured text pages by character offset; no opening the original file."""
        return await query(mapId, snapshotId, 'content', projectId, nodeId=nodeId, documentId=documentId, offset=offset, limit=limit)

    @server.tool(annotations=write)
    async def create_document_view(mapId: str, snapshotId: str, projectId: str | None = None,
                                   title: str = 'Дерево документов', documentId: str | None = None) -> dict:
        """Save an immutable bounded document graph with its original snapshot and locators."""
        project = project_scope(projectId)
        return bounded(await request('POST', '/api/document-maps/' + quote(mapId, safe='') + '/views',
                                     {'snapshotId': snapshotId, 'projectId': project, 'title': title, 'documentId': documentId}))
