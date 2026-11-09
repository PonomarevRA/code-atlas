"""Real stdio/HTTP SDK parity for the new tools; installed Atlas is not touched."""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_agent_mcp import atlas_server, unpack


def test_real_pattern_and_module_tools(atlas_server):
    """Negotiate tools and read matching source-bound HTTP/MCP results."""
    port, _ = atlas_server

    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "atlas.agent_mcp", "--port", str(port)],
            env={**os.environ, "HTTP_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1"},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                from atlas.agent_setup import TOOLS
                assert set(tools) == set(TOOLS)
                for name in ("search_code_patterns", "get_code_pattern", "analyze_code_modules"):
                    assert tools[name].annotations.readOnlyHint is True
                listing = unpack(await session.call_tool("search_code_patterns", {"limit": 1}))
                summary = listing["items"][0]
                card = unpack(await session.call_tool("get_code_pattern", {
                    "patternId": summary["id"], "contentHash": summary["contentHash"],
                    "version": summary["version"], "catalogVersion": listing["catalogVersion"],
                }))
                assert card["pattern"]["contentHash"] == summary["contentHash"]
                stale = await session.call_tool("get_code_pattern", {"patternId": summary["id"], "contentHash": "0" * 64})
                assert stale.isError
                report = unpack(await session.call_tool("analyze_code_modules", {
                    "projectId": "legacy-fixture", "snapshotId": "first", "algorithms": ["current"],
                }))
                assert report["snapshotId"] == "first" and report["readOnly"]
                assert report["projection"]["unitTotal"] >= 1
                feedback = unpack(await session.call_tool("prepare_test_feedback", {
                    "projectId": "legacy-fixture", "snapshotId": "first", "reportId": "junit-1",
                    "junitXml": '<testsuite><testcase name="Example"><failure>reported failure</failure></testcase></testsuite>',
                }))
                assert feedback["executionVerified"] is False
                assert feedback["resultsPresent"] is True
                assert "report-snapshot-unknown" in feedback["unknownReasons"]

    asyncio.run(scenario())
