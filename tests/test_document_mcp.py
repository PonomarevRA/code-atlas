"""Настоящий stdio MCP: агент строит документ один раз и читает captured snapshot."""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_agent_mcp import atlas_server, unpack


def test_document_mcp_stdio_pinned_tree_table_content_and_scopes(atlas_server, tmp_path):
    """Два клиента читают одинаковые locators; refresh не ломает исторический snapshot."""
    port, _ = atlas_server
    root = tmp_path / 'docs'; root.mkdir()
    source = root / 'guide.md'
    source.write_text('# Contract\n\n| Name | Price |\n| --- | --- |\n| Alpha | 42 |\n\nLong text ' + 'x' * 18000, encoding='utf-8')

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=['-m', 'atlas.agent_mcp', '--port', str(port)])
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {item.name: item for item in (await session.list_tools()).tools}
                assert tools['query_document_tree'].annotations.readOnlyHint is True
                assert tools['ensure_document_map'].annotations.readOnlyHint is False
                found = unpack(await session.call_tool('discover_document_files', {'root': str(root)}))
                assert found['built'] is False
                created = unpack(await session.call_tool('ensure_document_map', {'root': str(root), 'files': ['guide.md']}))
                map_id = created['map']['id']; job_id = created['job']['id']
                for _ in range(200):
                    job = unpack(await session.call_tool('get_map_job', {'jobId': job_id}))
                    if job['status'] not in {'queued', 'running'}:
                        break
                    await asyncio.sleep(.03)
                assert job['status'] == 'completed', job
                args = {'mapId': map_id, 'snapshotId': job['snapshotId']}
                tree = unpack(await session.call_tool('query_document_tree', args))
                assert tree['items'][0]['sourcePath'] == 'guide.md'
                graph = unpack(await session.call_tool('query_document_tree', {**args, 'operation': 'graph'}))
                table = next(node for node in graph['nodes'] if node['kind'] == 'table')
                table_args = {**args, 'nodeId': table['id'], 'documentId': table['documentId']}
                window = unpack(await session.call_tool('read_document_table', table_args))
                assert any(cell['text'] == '42' for cell in window['cells'])
                paragraph = next(node for node in graph['nodes'] if node.get('preview', '').startswith('Long text'))
                content = unpack(await session.call_tool('read_document_content',
                    {**args, 'nodeId': paragraph['id'], 'documentId': paragraph['documentId'], 'offset': 0, 'limit': 4000}))
                assert content['nextOffset'] == 4000 and len(content['text']) == 4000
                source.unlink()
                captured = unpack(await session.call_tool('read_document_table', table_args))
                assert captured['freshness']['status'] == 'stale'
                assert captured['cells'] == window['cells']
                saved = unpack(await session.call_tool('create_document_view', {**args, 'title': 'MCP fixture'}))
                assert saved['snapshotId'] == args['snapshotId'] and saved['url'].startswith('/documents.html')
                bad_scope = await session.call_tool('cancel_map_job', {'jobId': job_id, 'mapId': 'unrelated'})
                assert bad_scope.isError
                return args
    asyncio.run(scenario())
