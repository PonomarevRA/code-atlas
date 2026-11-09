"""Reproducible isolated load checks for chunk providers, snapshots and MCP APIs.

Run from the source checkout with its Python environment. Every corpus and store
is temporary; only JSON receipts are persisted. Separate subprocesses make RSS
peaks attributable to one case. Filesystem caches and host load are uncontrolled.
"""

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from hashlib import sha256
from importlib.metadata import version
from io import StringIO
import json
import math
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SCHEMA = 'atlas-chunk-load-v1'


def encoded(value):
    """Encode complete JSON facts deterministically, retaining sequence order."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def timings(samples):
    """Report nearest-rank percentiles with sample count rather than interpolated claims."""
    ordered = sorted(samples)
    return {'samples': len(samples), 'p50Ms': ordered[math.ceil(len(ordered) * .5) - 1],
            'p95Ms': ordered[math.ceil(len(ordered) * .95) - 1], 'maxMs': ordered[-1]}


def rss_peaks():
    """Return OS high-water marks; child RSS is max individual child, never a sum."""
    multiplier = 1 if sys.platform == 'darwin' else 1024
    return {'selfPeakRssBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * multiplier,
            'childPeakRssBytes': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * multiplier,
            'scope': 'Fresh case process high-water mark; child peak is not concurrent aggregate RSS.'}


def record_counter(rows):
    """Compare all fields and multiplicity, without weakening equivalence to counts."""
    return Counter(encoded(row) for row in rows)


def source_hashes():
    """Fingerprint all Atlas Python owners, standalone provider scripts and this harness."""
    paths = list((ROOT / 'atlas').rglob('*.py'))
    paths.extend((ROOT / 'resources/integrations/graphify').glob('*.py'))
    paths.append(Path(__file__).resolve())
    return {path.relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))}


def qualify_sources(item, before, after):
    """Invalidate an otherwise successful measurement when owning code changed during it."""
    changes = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    result = {**item, 'sourceHashes': before, 'sourceHashesAfter': after,
              'sourcesStableDuringCase': not changes, 'changedSources': changes}
    if changes and result['status'] == 'PASS':
        result['status'] = 'INVALID'
        result['reason'] = 'Owning source changed during measurement; rerun this case.'
    return result


def parse_spec(spec):
    """Reject unbounded or malformed workloads before allocating fixtures or log paths."""
    parts = spec.split(':')
    kind = parts[0]
    if kind in ('provider', 'provider-cache') and len(parts) == 3 and parts[1] in ('python', 'csharp', 'typescript'):
        if parts[2].isdigit() and 1 <= int(parts[2]) <= 1000:
            return kind, (parts[1], int(parts[2]))
    if kind == 'storage' and len(parts) == 2:
        if parts[1].isdigit() and 1 <= int(parts[1]) <= 50000:
            return kind, (int(parts[1]),)
    if kind == 'http' and len(parts) in (1, 3):
        if len(parts) == 1:
            return kind, (1000, 1)
        if (parts[1].isdigit() and parts[2].isdigit()
                and 1 <= int(parts[1]) <= 10000 and 1 <= int(parts[2]) <= 100):
            return kind, (int(parts[1]), int(parts[2]))
    raise ValueError('Unsupported or out-of-budget load case: ' + spec)


def run_child(command):
    """Bound a whole case and terminate its owned process group on timeout."""
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=600)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(command, 124, stdout, stderr + '\nOwned load case timed out.')


def make_corpus(root, language, count):
    """Create a deterministic cyclic multi-file corpus, never execute its contents."""
    root.mkdir()
    for index in range(count):
        following = (index + 1) % count
        if language == 'python':
            name = f'm{index}.py'
            text = (f'from m{following} import value{following}\n\n'
                    f'def value{index}():\n    """Return the next value in the cycle."""\n'
                    f'    return value{following}()\n')
        elif language == 'csharp':
            name = f'C{index}.cs'
            text = (f'namespace LoadFixture {{ public class C{index} {{ '
                    f'public static int Value() {{ return C{following}.Value(); }} }} }}\n')
        else:
            name = f'm{index}.ts'
            text = (f"import {{ value{following} }} from './m{following}';\n"
                    f'export function value{index}(): number {{ return value{following}(); }}\n')
        (root / name).write_text(text, encoding='utf-8')


def provider_case(language, count):
    """Run actual provider subprocesses and require exact raw node/edge parity."""
    from atlas.integrations.graphify.adapter import build_graph
    with tempfile.TemporaryDirectory(prefix='atlas-load-provider-') as folder:
        base = Path(folder)
        corpus = base / 'corpus'
        make_corpus(corpus, language, count)
        hashes = {p.name: sha256(p.read_bytes()).hexdigest() for p in corpus.iterdir()}
        results = {}
        raw_results = {}
        for label, chunked in [('monolithic', False), ('chunked', True)]:
            started = time.perf_counter()
            result = build_graph(corpus, base / label, chunked=chunked,
                                 chunk_max_files=8, chunk_target_bytes=16 * 1024)
            elapsed = (time.perf_counter() - started) * 1000
            raw = json.loads(Path(result['metadata']['rawGraphPath']).read_bytes())
            raw_results[label] = raw
            results[label] = {'wallMs': elapsed, 'nodes': len(raw['nodes']),
                              'edges': len(raw.get('edges', raw.get('links', []))),
                              'rawBytes': Path(result['metadata']['rawGraphPath']).stat().st_size}
            if chunked:
                results[label]['chunkAnalysis'] = result['metadata']['chunkAnalysis']
        left, right = raw_results['monolithic'], raw_results['chunked']
        node_equal = record_counter(left['nodes']) == record_counter(right['nodes'])
        left_edges = left.get('edges', left.get('links', []))
        right_edges = right.get('edges', right.get('links', []))
        edge_equal = record_counter(left_edges) == record_counter(right_edges)
        assert node_equal and edge_equal, 'Full provider facts/multiplicity differ'
        assert hashes == {p.name: sha256(p.read_bytes()).hexdigest() for p in corpus.iterdir()}
        assert not (corpus / 'graphify-out').exists(), 'Provider wrote into analyzed corpus'
        return {'case': 'provider', 'language': language, 'files': count, 'results': results,
                'nodeFactsEqual': node_equal, 'edgeFactsEqual': edge_equal,
                'corpusIdentity': sha256(encoded(hashes)).hexdigest(),
                'notes': 'Chunk path includes cache warming plus authoritative global resolution; not bounded global RSS.'}


def provider_cache_case(language, count):
    """Require warm reuse and complete fresh resolution after source/config changes."""
    from atlas.integrations.graphify.adapter import build_graph
    with tempfile.TemporaryDirectory(prefix='atlas-load-provider-cache-') as folder:
        base = Path(folder)
        corpus = base / 'corpus'
        make_corpus(corpus, language, count)
        phases, facts = {}, {}

        def build(label, chunked=True):
            """Capture actual extraction counters and complete facts for one source generation."""
            began = time.perf_counter()
            graph = build_graph(corpus, base / label, chunked=chunked,
                                chunk_max_files=8, chunk_target_bytes=16 * 1024)
            raw = json.loads(Path(graph['metadata']['rawGraphPath']).read_bytes())
            facts[label] = (record_counter(raw['nodes']),
                            record_counter(raw.get('edges', raw.get('links', []))))
            phases[label] = {'wallMs': (time.perf_counter() - began) * 1000,
                             'nodes': len(raw['nodes']), 'edges': len(raw.get('edges', raw.get('links', [])))}
            if chunked:
                report = graph['metadata']['chunkAnalysis']
                for name in ('fileExtractorCalls', 'configurationFingerprint', 'persistentCache',
                             'persistentContextBypassFiles', 'fileCount'):
                    phases[label][name] = report[name]
            return graph

        build('cold')
        build('warm')
        assert facts['cold'] == facts['warm'], 'Warm cache changed provider facts'
        if language in ('python', 'csharp'):
            assert phases['warm']['fileExtractorCalls'] == {'chunks': 0, 'global': 0}
            assert phases['warm']['persistentCache']['restoredEntries'] == count
        else:
            assert phases['warm']['persistentCache']['restoredEntries'] == 0
        suffix = {'python': '.py', 'csharp': '.cs', 'typescript': '.ts'}[language]
        first = corpus / (('C0' if language == 'csharp' else 'm0') + suffix)
        extra = {'python': '\ndef added0():\n    return 7\n',
                 'csharp': '\nnamespace LoadFixture { public class Extra0 { public int Added(){ return C1.Value(); } } }\n',
                 'typescript': '\nexport function added0(): number { return value1(); }\n'}[language]
        first.write_text(first.read_text() + extra, encoding='utf-8')
        build('source-change')
        build('source-reference', chunked=False)
        assert facts['source-change'] == facts['source-reference'], 'Changed source reused stale facts'
        if language in ('python', 'csharp'):
            assert phases['source-change']['fileExtractorCalls'] == {'chunks': 1, 'global': 0}
            assert phases['source-change']['persistentCache']['restoredEntries'] == count - 1
        (corpus / 'tsconfig.json').write_text('{"compilerOptions":{"strict":true,"baseUrl":"."}}')
        build('config-change')
        build('config-reference', chunked=False)
        assert facts['config-change'] == facts['config-reference'], 'Config change preserved stale resolution'
        assert phases['config-change']['configurationFingerprint'] != phases['cold']['configurationFingerprint']
        assert phases['config-change']['persistentCache']['restoredEntries'] == 0
        cache_files = list((base / '_provider-cache').rglob('*.gz'))
        return {'case': 'provider-cache', 'language': language, 'files': count, 'phases': phases,
                'allPhaseFactsEqualToReference': True,
                'retainedCompressedCacheBytes': sum(path.stat().st_size for path in cache_files),
                'notes': 'Persistent reuse is qualified only for Python/C# local extraction; TS deliberately bypasses it. Global resolution still executes.'}


def make_graph(count):
    """Construct repeated metadata plus distinct symbols and ordered parallel evidence."""
    nodes, edges = [], []
    facts = {'language': 'python', 'projectId': 'load-project', 'path': 'src',
             'description': 'Общие сведения проекта для повторного использования.'}
    for index in range(count):
        identifier = f'load_project_namespace_component_method_{index}'
        nodes.append({'id': identifier, 'label': f'Method{index}()', 'kind': 'method',
                      'file': f'src/file{index // 100}.py', 'line': index % 100 + 1,
                      'description': 'Проверяем сохранение полного текста и порядка. ' * 4,
                      'codeProjectFacts': facts})
        for offset in (1, 2, 2):
            edges.append({'source': identifier,
                          'target': f'load_project_namespace_component_method_{(index + offset) % count}',
                          'relation': 'calls', 'confidence': 'EXTRACTED',
                          'evidence': {'line': index % 100 + 1, 'offset': offset,
                                       'runtimeSelectionKnown': False}})
    return {'nodes': nodes, 'edges': edges, 'metadata': {'snapshotId': 'load', 'fixture': 'synthetic-v1'}}


def object_inventory(root):
    """Measure regular managed object files by digest name and logical length."""
    return {p.name: p.stat().st_size for p in root.rglob('*.json.gz')}


def read_case(path, mode):
    """Measure one fresh-process physical read, sampling RSS before equality serialization."""
    import psutil
    from atlas.snapshot_objects import read_snapshot
    process = psutil.Process()
    initial = process.memory_info().rss
    peak, stop = [initial], Event()

    def sample():
        """Observe retained and transient allocations during the physical read only."""
        while not stop.wait(.005):
            peak[0] = max(peak[0], process.memory_info().rss)

    sampler = Thread(target=sample, daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        graph = read_snapshot(path) if mode == 'compact' else json.loads(path.read_bytes())
        milliseconds = (time.perf_counter() - started) * 1000
        peak[0] = max(peak[0], process.memory_info().rss)
    finally:
        stop.set()
        sampler.join()
    return {'mode': mode, 'wallMs': milliseconds, 'initialRssBytes': initial,
            'sampledReadPeakRssBytes': peak[0], 'sampledReadRssDeltaBytes': peak[0] - initial,
            'graphHash': sha256(encoded(graph)).hexdigest(), 'nodes': len(graph['nodes']),
            'edges': len(graph['edges']), 'filesystemCache': 'uncontrolled'}


def storage_case(count, snapshots=5, reads=7):
    """Measure repeated snapshots, exact hydration and corruption rejection in isolation."""
    from atlas.snapshot_objects import read_snapshot, write_snapshot, object_root
    graph = make_graph(count)
    with tempfile.TemporaryDirectory(prefix='atlas-load-storage-') as folder:
        base = Path(folder)
        paths, writes, deltas, plain_bytes = [], [], [], 0
        previous = {}
        for generation in range(snapshots):
            graph['nodes'][0]['description'] = f'Changed generation {generation}'
            graph['metadata']['snapshotId'] = f'load-{generation}'
            path = base / f'snapshot-{generation}' / 'atlas-graph.json'
            started = time.perf_counter()
            write_snapshot(path, graph)
            writes.append((time.perf_counter() - started) * 1000)
            current = object_inventory(object_root(path))
            deltas.append({'generation': generation, 'newObjects': len(current.keys() - previous.keys()),
                           'newObjectBytes': sum(current[k] for k in current.keys() - previous.keys()),
                           'reusedObjects': len(current.keys() & previous.keys())})
            previous = current
            paths.append(path)
            plain_bytes += len(encoded(graph))
            assert read_snapshot(path) == graph, 'Snapshot lost facts or ordering'
        samples = []
        for _ in range(reads):
            started = time.perf_counter()
            hydrated = read_snapshot(paths[-1])
            samples.append((time.perf_counter() - started) * 1000)
            assert hydrated == graph
        manifest_bytes = sum(path.stat().st_size for path in paths)
        retained_bytes = sum(previous.values()) + manifest_bytes
        assert retained_bytes < plain_bytes, 'Synthetic repeated history did not reduce logical storage'
        assert all(row['reusedObjects'] for row in deltas[1:]), 'Unchanged chunks were not reused'
        plain_path = base / 'plain.json'
        plain_path.write_bytes(encoded(graph))
        physical_reads = []
        for mode, path in [('plain', plain_path), ('compact', paths[-1])]:
            measured = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                      '--read-path', str(path), '--read-mode', mode],
                                     cwd=ROOT, capture_output=True, text=True, timeout=120, check=True)
            physical_reads.append(json.loads(measured.stdout))
        assert physical_reads[0]['graphHash'] == physical_reads[1]['graphHash']
        reference = json.loads(paths[-1].read_bytes())['_snapshotManifest']['nodes']['chunks'][0]
        blob = object_root(paths[-1]) / reference['hash'][:2] / (reference['hash'] + '.json.gz')
        blob.write_bytes(blob.read_bytes() + b'trailing-corruption')
        try:
            read_snapshot(paths[-1])
        except (ValueError, OSError):
            corruption_rejected = True
        else:
            raise AssertionError('Corrupt object was silently accepted')
        return {'case': 'storage', 'nodes': count, 'edges': len(graph['edges']),
                'snapshots': snapshots, 'plainHistoryBytes': plain_bytes,
                'retainedObjectBytes': sum(previous.values()), 'manifestBytes': manifest_bytes,
                'retainedTotalBytes': retained_bytes, 'retainedToPlainRatio': retained_bytes / plain_bytes,
                'writeLatency': timings(writes), 'readLatency': timings(samples),
                'freshProcessPhysicalReads': physical_reads,
                'objectDeltas': deltas, 'corruptionRejected': corruption_rejected,
                'notes': 'Synthetic logical lengths; compression and metadata repetition are fixture-specific. Reads hydrate full graphs.'}


def setup_http(base, edge_count):
    """Register real routes over an isolated real ProjectStore with a captured fixture."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from atlas.analysis_contexts import AnalysisContexts
    from atlas.graph_analysis_api import register_graph_analysis_routes
    from atlas.snapshot_objects import write_snapshot
    from atlas.store import ProjectStore, StoreError
    from atlas import method_flow_chunks
    source = base / 'source'
    source.mkdir()
    (source / 'run.ts').write_text('function Run(){\n' + 'Work();\n' * 300 + '}\n')
    state = ProjectStore(base / 'state')
    project, snapshot = 'a' * 16, 'b' * 32
    path = state.data_dir / 'maps/projects' / project / snapshot / 'atlas-graph.json'
    nodes = [{'id': 'root', 'label': 'Run()', 'kind': 'method', 'file': 'run.ts', 'line': 1}]
    nodes.extend({'id': f'n{i}', 'label': f'Target{i}', 'kind': 'external'} for i in range(127))
    edges = [{'source': 'root', 'target': f'n{i % 127}', 'relation': 'calls', 'sequence': i}
             for i in range(edge_count)]
    write_snapshot(path, {'nodes': nodes, 'edges': edges, 'metadata': {'snapshotId': snapshot}})
    state.projects[project] = {'id': project, 'name': 'Load', 'path': str(source),
                               'status': 'completed', '_graph': str(path), '_rawGraph': str(path)}
    app = FastAPI()
    contexts = app.state.analysis_contexts = AnalysisContexts()

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        """Use the same domain-to-HTTP mapping as the production composition root."""
        return JSONResponse(status_code=error.status, content={'detail': str(error)})

    calls = [0]
    original = method_flow_chunks._parse

    def counted_parse(*args):
        """Count real grammar parses without changing their output or failure behavior."""
        calls[0] += 1
        return original(*args)

    register_graph_analysis_routes(app, state)
    return app, state, contexts, project, snapshot, source, calls, counted_parse


def http_case(edge_count=1000, workers=8, rounds=1):
    """Exercise actual HTTP handlers, parallel pagination and MCP-to-ASGI proxy calls."""
    import httpx
    from fastapi.testclient import TestClient
    from atlas import agent_mcp, method_flow_chunks
    with tempfile.TemporaryDirectory(prefix='atlas-load-http-') as folder:
        app, state, contexts, project, snapshot, source, parses, parser = setup_http(Path(folder), edge_count)
        base = f'/api/projects/{project}'
        common = {'snapshotId': snapshot, 'nodeId': 'root'}
        try:
            with TestClient(app) as client, patch.object(method_flow_chunks, '_parse', parser):
                latencies, page_sizes = [], []

                def neighbors(_):
                    """Read an entire neighborhood with its returned cursor and exact sequence evidence."""
                    cursor, seen, samples = None, [], []
                    while True:
                        payload = {**common, 'direction': 'outgoing', 'limit': 120}
                        if cursor:
                            payload['cursor'] = cursor
                        started = time.perf_counter()
                        response = client.post(base + '/graph-neighbors', json=payload)
                        samples.append((time.perf_counter() - started) * 1000)
                        assert response.status_code == 200, response.text
                        page = response.json()
                        assert len(response.content) <= 64000
                        page_sizes.append(len(response.content))
                        seen.extend(edge['sequence'] for edge in page['edges'])
                        cursor = page['nextRef']
                        if not cursor:
                            break
                    assert seen == list(range(edge_count)), 'Pagination lost or duplicated evidence'
                    return samples

                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for samples in pool.map(neighbors, range(workers * rounds)):
                        latencies.extend(samples)
                identifiers = []
                for _ in range(workers):
                    response = client.post(base + '/analysis-contexts', json={'snapshotId': snapshot})
                    assert response.status_code == 200, response.text
                    identifiers.append(response.json()['contextId'])
                assert client.post(base + '/analysis-contexts', json={'snapshotId': snapshot}).status_code == 429
                context = identifiers[0]
                chunk_body = {**common, 'contextId': context, 'limit': 40}
                page = client.post(base + '/method-flow-chunk', json=chunk_body)
                assert page.status_code == 200, page.text
                block = next(item['ref'] for item in page.json()['children'] if item['type'] == 'statement_block')
                for _ in range(12):
                    page = client.post(base + '/method-flow-chunk', json={**chunk_body, 'chunkRef': block})
                    assert page.status_code == 200, page.text
                cached_parse_count = parses[0]
                assert cached_parse_count == 1, f'Unexpected repeated parse count {cached_parse_count}'
                stale = client.post(base + '/graph-neighbors', json={**common, 'snapshotId': 'stale'})
                invalid = client.post(base + '/graph-neighbors', json={**common, 'cursor': 'broken'})
                assert stale.status_code == 409 and invalid.status_code == 400
                for identifier in identifiers:
                    closed = client.post(base + f'/analysis-contexts/{identifier}/close', json={'snapshotId': snapshot})
                    assert closed.status_code == 200
                assert client.post(base + '/method-flow-chunk', json=chunk_body).status_code == 410

                async def mcp_roundtrip():
                    """Call real FastMCP tools through their HTTP proxy and isolated ASGI transport."""
                    original_client = httpx.AsyncClient

                    def local_client(**kwargs):
                        """Keep serialization and proxy behavior, replacing only the socket transport."""
                        return original_client(transport=httpx.ASGITransport(app=app), **kwargs)

                    def data(result):
                        """Read SDK structured results across supported tuple/content result shapes."""
                        if isinstance(result, tuple):
                            return result[1] if len(result) > 1 and result[1] else json.loads(result[0][0].text)
                        if isinstance(result, dict):
                            return result
                        if isinstance(result, list):
                            return json.loads(result[0].text)
                        value = getattr(result, 'structuredContent', None)
                        return value or json.loads(result.content[0].text)

                    samples = []
                    with patch.object(agent_mcp.httpx, 'AsyncClient', local_client):
                        mcp = agent_mcp.create_mcp(8877, project_id=project)
                        for _ in range(10):
                            started = time.perf_counter()
                            first = data(await mcp.call_tool('get_neighbors', {**common, 'limit': 120}))
                            assert bool(first['nextRef']) == (first['total'] > len(first['edges']))
                            opened = data(await mcp.call_tool('open_analysis_context', {'snapshotId': snapshot}))
                            result = data(await mcp.call_tool('get_analysis_section', {
                                **common, 'contextId': opened['contextId'], 'section': 'methodFlow'}))
                            assert result['analysisKind'] == 'description-section'
                            await mcp.call_tool('close_analysis_context', {
                                'snapshotId': snapshot, 'contextId': opened['contextId']})
                            samples.append((time.perf_counter() - started) * 1000)
                    return timings(samples)

                mcp_latency = asyncio.run(mcp_roundtrip())
                return {'case': 'http', 'edgesPerTraversal': edge_count, 'parallelTraversals': workers,
                        'completedTraversals': workers * rounds,
                        'httpPageLatency': timings(latencies), 'maxHttpPageBytes': max(page_sizes),
                        'astReadsIncludingRoot': 13, 'retainedAstParseCalls': cached_parse_count,
                        'mcpFourToolCycleLatency': mcp_latency,
                        'negatives': {'capacity': 429, 'staleSnapshot': stale.status_code,
                                      'invalidCursor': invalid.status_code, 'closedContext': 410},
                        'notes': 'Real FastAPI/TestClient and FastMCP/HTTP serialization via ASGI; excludes stdio/socket/native app overhead.'}
        finally:
            contexts.close()
            state.close()


def child_case(spec):
    """Run one case and append fresh-process resource accounting and qualifications."""
    kind, parts = parse_spec(spec)
    if kind == 'provider':
        result = provider_case(*parts)
    elif kind == 'provider-cache':
        result = provider_cache_case(*parts)
    elif kind == 'storage':
        result = storage_case(*parts)
    elif kind == 'http':
        result = http_case(edge_count=parts[0], rounds=parts[1])
    else:
        raise ValueError('Unknown load case')
    result['rss'] = rss_peaks()
    result['status'] = 'PASS'
    return result


def main():
    """Write incremental attributable receipts; never hide a failed case behind later success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--case', action='append', dest='cases')
    parser.add_argument('--child')
    parser.add_argument('--read-path', type=Path)
    parser.add_argument('--read-mode', choices=['plain', 'compact'])
    args = parser.parse_args()
    if args.read_path:
        if args.read_mode is None:
            parser.error('--read-mode is required with --read-path')
        print(json.dumps(read_case(args.read_path, args.read_mode)))
        return
    if args.child:
        with redirect_stdout(StringIO()):
            result = child_case(args.child)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.output_dir is None:
        parser.error('--output-dir is required')
    output = args.output_dir.resolve()
    cases = args.cases or [f'provider:{language}:{count}' for count in (50, 200)
                           for language in ('python', 'csharp', 'typescript')]
    if args.cases is None:
        cases.extend([f'provider-cache:{language}:200' for language in ('python', 'csharp', 'typescript')])
        cases.extend(['storage:1000', 'storage:5000', 'storage:20000', 'http:1:1', 'http', 'http:1000:20'])
    for case in cases:
        parse_spec(case)
    output.mkdir(parents=True, exist_ok=True)
    receipt = {'schema': SCHEMA, 'machine': {'platform': platform.platform(), 'python': sys.version,
               'cpuCount': os.cpu_count(), 'providerVersion': version('graphifyy')},
               'harnessSha256': sha256(Path(__file__).read_bytes()).hexdigest(), 'cases': []}
    receipt['sourceHashes'] = source_hashes()
    for index, case in enumerate(cases):
        started = time.perf_counter()
        before = source_hashes()
        result = run_child([sys.executable, str(Path(__file__).resolve()), '--child', case])
        log = output / f'load-{index:02d}-{case.replace(":", "-")}.log'
        log.write_text(result.stderr, encoding='utf-8')
        if result.returncode:
            item = {'spec': case, 'status': 'FAIL', 'exitCode': result.returncode,
                    'stderrTail': result.stderr[-6000:], 'wallSeconds': time.perf_counter() - started}
        else:
            item = {'spec': case, **json.loads(result.stdout), 'wallSeconds': time.perf_counter() - started}
        item = qualify_sources(item, before, source_hashes())
        receipt['cases'].append(item)
        (output / 'load-results.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'case': case, 'status': item['status'], 'seconds': round(item['wallSeconds'], 3)}), flush=True)
    if any(item['status'] != 'PASS' for item in receipt['cases']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
