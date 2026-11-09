"""Stdio discovery stays quiet while SDK warnings remain visible to MCP clients."""

import asyncio
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_agent_mcp import atlas_server


def test_stdio_discovery_suppresses_request_noise_and_preserves_warnings(tmp_path: Path, atlas_server):
    """Use real loopback HTTP while retaining a real SDK warning on stderr."""
    log_path = tmp_path / 'mcp-stderr.log'
    port, _ = atlas_server

    async def scenario():
        """Discover tools, ping the process and exercise an unknown-tool warning."""
        parameters = StdioServerParameters(command=sys.executable,
                                          args=['-m', 'atlas.agent_mcp', '--port', str(port)])
        with log_path.open('w') as error_log:
            async with stdio_client(parameters, errlog=error_log) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    catalog = await session.list_tools()
                    assert 'describe_node' in {tool.name for tool in catalog.tools}
                    await session.list_resources()
                    await session.list_prompts()
                    await session.send_ping()
                    projects = await session.call_tool('list_projects', {})
                    assert not projects.isError
                    failure = await session.call_tool('missing-atlas-tool', {})
                    assert failure.isError

    asyncio.run(scenario())
    messages = log_path.read_text()
    assert 'Processing request of type' not in messages
    assert 'HTTP Request:' not in messages
    assert "Tool 'missing-atlas-tool' not listed" in messages
