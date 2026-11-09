"""Reproduce selected-view allocation and real stdio request timings; never rebuild projects."""

import argparse
import asyncio
from copy import deepcopy
import gc
import json
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from atlas.agent_views import _view_nodes
from atlas.project_scopes import assign_code_projects


def legacy_view_nodes(graph, selected):
    """Reproduce the pre-review full-payload copy and project assignment for comparison."""
    copied = deepcopy(graph['nodes'])
    projects = assign_code_projects(copied)
    by_id = {node['id']: node for node in copied}
    return projects, [by_id[node_id] for node_id in selected]


def allocation_benchmark():
    """Compare identical owners, counts and selected nested facts on a fixed synthetic snapshot."""
    graph = {'nodes': [{'id': 'manifest', 'file': 'src/pyproject.toml', 'kind': 'file'}]}
    for index in range(8000):
        graph['nodes'].append({'id': f'n{index}', 'file': f'src/module{index // 20}.py',
                               'kind': 'method', 'testFacts': {'sources': [
                                   {'id': row, 'bindings': [{'name': 'value', 'value': index + row}]}
                                   for row in range(12)]}})
    selected = ['n0', 'n7999']
    assert legacy_view_nodes(graph, selected) == _view_nodes(graph, selected)
    measurements = {}
    for name, implementation in [('before', legacy_view_nodes), ('after', _view_nodes)]:
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            implementation(graph, selected)
            timings.append((time.perf_counter() - started) * 1000)
        gc.collect()
        tracemalloc.start()
        implementation(graph, selected)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        measurements[name] = {'medianMs': statistics.median(timings), 'runsMs': timings,
                              'peakAllocatedBytes': peak}
    return {'nodes': len(graph['nodes']), 'selectedNodes': len(selected),
            'rowsPerNode': 12, 'stage': 'project ownership and selected-card copy only', **measurements}


async def stdio_benchmark(args):
    """Time first and repeated SDK calls within one process/pool and one immutable snapshot."""
    parameters = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'atlas.agent_mcp', '--port', str(args.port)])
    timings, sizes = [], []
    started = time.perf_counter()
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            handshake = (time.perf_counter() - started) * 1000
            for _ in range(11):
                started = time.perf_counter()
                result = await session.call_tool('search_nodes', {'projectId': args.project,
                    'snapshotId': args.snapshot, 'query': 'LoopbackTransport', 'limit': 8})
                timings.append((time.perf_counter() - started) * 1000)
                if result.isError:
                    raise RuntimeError(str(result.content))
                data = result.structuredContent or json.loads(result.content[0].text)
                assert data['snapshotId'] == args.snapshot
                sizes.append(len(json.dumps(data, ensure_ascii=False).encode('utf-8')))
    return {'snapshotId': args.snapshot, 'projectId': args.project, 'tool': 'search_nodes',
            'query': 'LoopbackTransport', 'limit': 8, 'handshakeMs': handshake,
            'firstRequestMs': timings[0], 'warmMedianMs': statistics.median(timings[1:]),
            'runsMs': timings, 'responseBytes': sizes,
            'cacheBoundary': 'new MCP process and HTTP pool; Atlas server may already be warm'}


def main():
    """Write reproducible JSON evidence; optional loopback requests require an explicit snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project')
    parser.add_argument('--snapshot')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if bool(args.project) != bool(args.snapshot):
        parser.error('--project and --snapshot must be supplied together')
    result = {'viewAllocation': allocation_benchmark()}
    if args.project:
        result['realStdio'] = asyncio.run(stdio_benchmark(args))
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
