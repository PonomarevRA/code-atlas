"""Verify actual temporary Atlas facts through the source stdio MCP client."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def unpack(result):
    """Read a real SDK tool response, rejecting errors instead of reporting a false pass."""
    if result.isError:
        raise RuntimeError(str(result.content)[:500])
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    for block in result.content:
        if block.type == 'text':
            return json.loads(block.text)
    raise RuntimeError('Tool response has no JSON facts')


async def probe(args):
    """Read a real private snapshot and a bounded working-tree baseline without canonical mutation."""
    parameters = StdioServerParameters(command=sys.executable,
        args=['-m', 'atlas.agent_mcp', '--port', str(args.port)],
        env={**os.environ, 'HTTP_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1'})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tool_names = sorted(tool.name for tool in (await client.list_tools()).tools)
            metadata = unpack(await client.call_tool('get_checkout_map', {'sessionId': args.session}))
            snapshot = metadata['project'].get('snapshotId')
            if not snapshot or not metadata['project'].get('snapshotAvailable'):
                raise RuntimeError('Actual temporary map is not ready')
            listing = unpack(await client.call_tool('search_checkout_nodes', {
                'sessionId': args.session, 'snapshotId': snapshot, 'query': args.query, 'limit': 8}))
            if not listing['nodes']:
                raise RuntimeError('Actual map has no matching raw nodes')
            node = listing['nodes'][0]
            description = unpack(await client.call_tool('describe_checkout_node', {
                'sessionId': args.session, 'snapshotId': snapshot, 'nodeId': node['id']}))
            health = unpack(await client.call_tool('get_checkout_code_health', {
                'sessionId': args.session, 'snapshotId': snapshot, 'sourceMode': 'working-tree',
                'files': [args.health_file], 'maxMethods': 8}))
            after = unpack(await client.call_tool('get_checkout_map', {'sessionId': args.session}))
            result = {'kind': 'actual-source-mcp-checkout-proof', 'toolNames': tool_names,
                      'before': metadata, 'search': listing, 'description': description,
                      'health': health, 'after': after, 'installedRuntimeQualified': False}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps({'sessionId': args.session, 'snapshotId': snapshot,
                'toolCount': len(tool_names), 'node': node['id'], 'assessmentId': health.get('assessmentId'),
                'ttlExtended': after['expiresAt'] > metadata['expiresAt'],
                'analysisStatus': metadata['project'].get('analysisStatus'), 'output': str(args.output)}))


def main():
    """Select an explicitly supplied local probe target; never discover or restart installed Atlas."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--query', default='compute_module_projection')
    parser.add_argument('--health-file', default='atlas/module_compute.py')
    parser.add_argument('--output', type=Path, default=Path('outputs/checkout-maps/source-proof.json'))
    asyncio.run(probe(parser.parse_args()))


if __name__ == '__main__':
    main()
