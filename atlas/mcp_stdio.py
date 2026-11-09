"""MCP stdio ownership: UTF-8 transport must not close process standard handles."""

import sys

import anyio
from mcp.server.stdio import stdio_server


async def run_stdio(server) -> None:
    """Run FastMCP with explicit streams so SDK wrappers cannot close frozen-process stdout.

    The SDK's default temporary TextIOWrapper owns sys.stdout.buffer. Its finalizer
    can close that handle before PyInstaller shutdown uses it. Passing the existing
    streams uses the SDK's supported transport injection and leaves their ownership
    with the process. The low-level run call mirrors FastMCP.run_stdio_async.
    """
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8")
    async with stdio_server(anyio.wrap_file(sys.stdin), anyio.wrap_file(sys.stdout)) as (read, write):
        protocol = server._mcp_server
        await protocol.run(read, write, protocol.create_initialization_options())
