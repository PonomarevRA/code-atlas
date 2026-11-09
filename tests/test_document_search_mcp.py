"""Real stdio negatives prove project/data-map scope checks before HTTP gateway access."""
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def unpack(result):
    """Read the JSON payload only after MCP reported a successful tool call."""
    assert not result.isError, result
    return json.loads(next(content.text for content in result.content if content.type == 'text'))


async def scenario(arguments, callback):
    """Launch an actual stdio MCP server without a loopback HTTP listener."""
    script = Path(__file__).parent / 'fixtures' / 'document_search_stdio.py'
    parameters = StdioServerParameters(command=sys.executable, args=[str(script), *arguments])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await callback(session)


def test_search_stdio_requires_registered_project_and_preserves_fixed_scope():
    """Unscoped calls fail; a fixed-project connection injects its own authorized ID."""
    async def unscoped(session):
        """A missing project fails before the observable fake HTTP gateway."""
        result = await session.call_tool('build_document_search', {'mapId': 'm', 'snapshotId': 'd', 'codeSnapshotId': 'c'})
        assert result.isError
        assert 'registered projectId' in str(result.content)

    async def fixed(session):
        """Foreign project overrides fail and valid calls retain both pinned snapshot IDs."""
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert tools['build_document_search'].annotations.readOnlyHint is False
        assert tools['search_captured_documents'].annotations.readOnlyHint is True
        scope = {'mapId': 'm', 'snapshotId': 'd', 'codeSnapshotId': 'c'}
        rejected = await session.call_tool('search_captured_documents', {**scope, 'projectId': 'foreign', 'query': 'x'})
        assert rejected.isError
        accepted = unpack(await session.call_tool('build_document_search', scope))
        assert accepted['gatewayReached'] and accepted['body']['projectId'] == 'fixed'
        assert accepted['body']['snapshotId'] == 'd' and accepted['body']['codeSnapshotId'] == 'c'
        too_many = await session.call_tool('search_captured_documents', {**scope, 'query': 'x', 'limit': 51})
        assert too_many.isError
    asyncio.run(scenario([], unscoped))
    asyncio.run(scenario(['--project', 'fixed'], fixed))


def test_search_stdio_fixed_data_map_cannot_access_independent_document_search():
    """Both write and read document-search operations reject a data-map-only connection."""
    async def fixed_map(session):
        """No fake gateway response can be produced for any captured search operation."""
        scope = {'mapId': 'm', 'snapshotId': 'd', 'codeSnapshotId': 'c', 'projectId': 'p'}
        for tool, arguments in [('build_document_search', scope), ('get_document_search_index', scope),
                                ('search_captured_documents', {**scope, 'query': 'x'})]:
            result = await session.call_tool(tool, arguments)
            assert result.isError and 'limited to a data map' in str(result.content)
    asyncio.run(scenario(['--data-map', 'fixed-map'], fixed_map))
