"""Real stdio MCP and loopback HTTP parity for source-position dependencies."""

import asyncio
import os
import socket
import sys
from threading import Thread
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
import uvicorn

from test_agent_mcp import unpack
from test_position_context_api import position_api


@pytest.fixture
def position_http(position_api):
    """Expose the focused host fixture on an owned ephemeral socket; always drain its thread."""
    client, published, manager, _, sid = position_api
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(client.app, log_level='error', lifespan='off'))
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 8
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            assert server.started, 'Owned fixture server did not become ready'
            yield port, sid, published
        finally:
            server.should_exit = True
            thread.join(timeout=8)
            assert not thread.is_alive(), 'Owned fixture HTTP thread failed to stop'


def test_position_tools_real_sdk_and_http(position_http):
    """Discover96 source tools and verify both new scopes through actual MCP transport."""
    port, sid, published = position_http

    async def scenario():
        """Keep HTTP references and stdio requests on the same immutable fixture snapshot."""
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'atlas.agent_mcp', '--port', str(port)],
            env={**os.environ, 'HTTP_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as sdk:
                await sdk.initialize()
                tools = {tool.name: tool for tool in (await sdk.list_tools()).tools}
                from atlas.agent_setup import TOOLS
                assert set(tools) == set(TOOLS)
                for name in ['get_position_dependencies', 'get_checkout_position_dependencies']:
                    assert tools[name].annotations.readOnlyHint is True
                    assert tools[name].inputSchema['properties']['depth']['default'] == 2
                body = {'snapshotId': 'one', 'file': 'sample.py', 'line': 5}
                async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
                    for tool, identity, path in [
                        ('get_position_dependencies', {'projectId': 'permanent'}, '/api/projects/permanent'),
                        ('get_checkout_position_dependencies', {'sessionId': sid}, '/api/checkout-sessions/'+sid),
                    ]:
                        direct = await http.post(f'http://127.0.0.1:{port}'+path+'/position-dependencies', json=body)
                        assert direct.status_code == 200, direct.text
                        returned = unpack(await sdk.call_tool(tool, {**body, **identity}))
                        assert returned['anchorId'] == direct.json()['anchorId'] == 'middle'
                        for key in ['snapshotId', 'status', 'nodes', 'edges', 'incoming', 'outgoing']:
                            assert returned[key] == direct.json()[key]
                    for invalid in [{'depth': 9}, {'line': True}, {'snapshotId': 'old'},
                                    {'expectedSourceHash': '0' * 64}]:
                        failure = await sdk.call_tool('get_checkout_position_dependencies',
                            {**body, 'sessionId': sid, **invalid})
                        assert failure.isError

        # A fresh bound client must refuse foreign/private scope before host reads.
        bound_parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'atlas.agent_mcp', '--port', str(port), '--project-id', 'permanent'],
            env=parameters.env)
        before = published.reads
        async with stdio_client(bound_parameters) as (reader, writer):
            async with ClientSession(reader, writer) as sdk:
                await sdk.initialize()
                for tool, identity in [
                    ('get_position_dependencies', {'projectId': 'other'}),
                    ('get_checkout_position_dependencies', {'sessionId': sid}),
                ]:
                    result = await sdk.call_tool(tool, {**body, **identity})
                    assert result.isError
        assert published.reads == before

    asyncio.run(scenario())
