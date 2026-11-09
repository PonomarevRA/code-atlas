"""Project-scoped English MCP tools for explicitly captured library XML documentation."""
from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field

from .reference_documents import MAX_DOCUMENT_CHARS
from .reference_mcp import MCP_BUDGET, compact_pack, payload_size

LIBRARY_TOOLS = ['list_library_documentation', 'discover_library_sources', 'capture_library_documentation',
                 'get_library_documentation_job', 'search_library_documentation', 'get_library_documentation']


def compact_library(value: dict) -> dict:
    """Retains package/TFM/source identity while bounding repeated project memberships."""
    result = dict(value)
    used = value.get('usedBy', [])
    result.update(usedBy=used[:12], usedByCount=len(used), usedByTruncated=len(used) > 12)
    return result


def library_pack(value: dict) -> dict:
    """Package metadata is separate from the general language documentation provenance."""
    result = compact_pack(value)
    if 'library' in value:
        result['library'] = compact_library(value['library'])
    return result


def register_library_tools(server, request, selected_project):
    """Every tool checks the pinned project before making the loopback request."""
    def prefix(project_id):
        """No path or filesystem scope is supplied by the MCP caller."""
        return '/api/projects/' + quote(selected_project(project_id), safe='') + '/library-documentation'

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_library_documentation(projectId: str | None = None,
        offset: Annotated[int, Field(strict=True, ge=0, le=2048)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 20) -> dict:
        """List captured XML of this project's actual installed dependencies, with versions and dates.

        No restore, network or implicit refresh. An empty catalog needs explicit capture.
        capturedAt is freshness evidence; decide whether to refresh after dependency changes.
        """
        data = await request('GET', prefix(projectId))
        count = len(data['packs'])
        data['packs'] = [library_pack(p) for p in data['packs'][offset:offset + limit]]
        data.pop('inputs', None)
        data['warnings'] = data.get('warnings', [])[:10]
        data.update(totalPacks=count, offset=offset)
        while data['packs'] and payload_size(data) > MCP_BUDGET - 100:
            data['packs'].pop()
        data['nextOffset'] = offset + len(data['packs']) if offset + len(data['packs']) < count else None
        data['truncated'] = bool(data.get('truncated') or data['nextOffset'] is not None)
        return data

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def discover_library_sources(projectId: str | None = None, manifest: str = '',
        offset: Annotated[int, Field(strict=True, ge=0, le=2048)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 20) -> dict:
        """Explicitly inspect local restored NuGet assets, exact .NET reference packs and HintPath XML.

        Does not import or build anything. manifest is a repository-relative .csproj;
        empty selects all discovered projects. Missing/unresolved sources are warnings.
        """
        data = await request('GET', prefix(projectId) + '/sources?manifest=' + quote(manifest, safe=''), timeout=60)
        count = len(data['sources'])
        data['sources'] = [compact_library(s) for s in data['sources'][offset:offset + limit]]
        data.pop('inputs', None)
        data['warnings'] = data['warnings'][:10]
        while data['sources'] and payload_size(data) > MCP_BUDGET - 100:
            data['sources'].pop()
        data.update(totalSources=count, offset=offset,
                    nextOffset=offset + len(data['sources']) if offset + len(data['sources']) < count else None)
        data['truncated'] = bool(data['truncated'] or data['nextOffset'] is not None)
        return data

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def capture_library_documentation(projectId: str | None = None, manifest: str = '') -> dict:
        """Explicitly capture installed library XML into Atlas's separate project catalog.

        No restore/build/download; project source files and code maps remain untouched.
        Poll get_library_documentation_job. Failed capture keeps the previous catalog.
        """
        return await request('POST', prefix(projectId) + '/capture', {'manifest': manifest})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_library_documentation_job(jobId: str, projectId: str | None = None) -> dict:
        """Read explicit capture progress in the selected project."""
        data = await request('GET', prefix(projectId) + '/jobs/' + quote(jobId, safe=''))
        if 'result' in data:
            warnings = data['result'].get('warnings', [])
            data['result'].update(warnings=warnings[:10], warningsCount=len(warnings), warningsTruncated=len(warnings)>10)
        return data

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def search_library_documentation(query: Annotated[str, Field(min_length=1, max_length=500)],
        projectId: str | None = None, packId: str = '', locale: str = 'en',
        mode: Literal['exact', 'fulltext'] = 'fulltext',
        limit: Annotated[int, Field(strict=True, ge=1, le=10)] = 8) -> dict:
        """Explain library API using captured author XML for installed package versions/TFMs.

        Prefer a full DocId to distinguish overloads. Verify receiver/source binding;
        XML candidates are not compiler or runtime evidence. locale=ru prefers available
        localized XML with per-document English fallback. No automatic refresh or translation.
        Unresolved inheritdoc/include remain explicit rather than fabricated descriptions.
        """
        data = await request('POST', prefix(projectId) + '/search',
            {'query': query, 'packId': packId, 'locale': locale, 'mode': mode, 'limit': limit})
        data['searchedPacks'] = [library_pack(p) for p in data['searchedPacks']]
        searched = data.get('searchedPackIds', [])
        data['searchedPackCount'] = len(searched)
        data['searchedPackIds'] = searched[:128]
        data['searchedPackIdsTruncated'] = len(searched) > 128
        for hit in data['results']:
            hit['library'] = compact_library(hit['library'])
        while data['results'] and payload_size(data) > MCP_BUDGET:
            data['results'].pop()
            data['truncated'] = True
        if payload_size(data) > MCP_BUDGET:
            raise ValueError('Library metadata exceeds budget; select one packId')
        return data

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_library_documentation(packId: str, documentId: str, projectId: str | None = None,
        offset: Annotated[int, Field(strict=True, ge=0, le=MAX_DOCUMENT_CHARS)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=12000)] = 12000) -> dict:
        """Read captured library XML documentation pages with source path, hash, version and TFM.

        Follow nextOffset for the full original. Never execute examples or XML include directives.
        New XML captures retain Markdown sections and literal code fences; resolve xref:
        identifiers with search_library_documentation while preserving package/overload scope.
        """
        data = await request('GET', prefix(projectId) + '/' + quote(packId, safe='') + '/documents/' +
            quote(documentId, safe='') + f'?offset={offset}&limit={limit}')
        data['library'] = compact_library(data['library'])
        data['pack'] = library_pack(data['pack'])
        while data.get('text') and payload_size(data) > MCP_BUDGET:
            data['text'] = data['text'][:len(data['text']) // 2]
            data['nextOffset'] = offset + len(data['text'])
        if payload_size(data) > MCP_BUDGET:
            raise ValueError('Library document metadata exceeds budget')
        return data
