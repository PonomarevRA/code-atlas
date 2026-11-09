#!/usr/bin/env python3
"""Repeat paired warm-map baseline/missing-engine/synthetic-overlay measurements.

Developer harness only: temporary source-pinned stores and a synthetic provider,
not a native engine benchmark. No SDK installation or analyzed-project build.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import argparse
import gc
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

# Direct execution from tools/ should import this checkout, not a system Atlas.
REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

import psutil
from atlas import method_flow_chunks, semantic_requests, semantic_sources
from atlas import store as store_module
from atlas.semantic_enrichment import SemanticEnrichment
from atlas.semantic_models import (SemanticAvailability, SemanticCoverage, SemanticFact,
                                   SemanticResult, SemanticSymbol, SourceSpan)
from atlas.semantic_runtime import ProbeOutcome

SOURCES = {'a.py': b'from b import work\n\ndef run():\n    return work()\n',
           'b.py': b'def work():\n    return 1\n'}
MODES = ('baseline', 'missing_engine', 'applied_synthetic_overlay')


def percentile(values, fraction):
    """Use a reproducible nearest-rank percentile; this is descriptive, not a confidence interval."""
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def drain(service):
    """Wait outside measured intervals for this harness's already scheduled background work."""
    if service is None:
        return
    service.executor.submit(lambda: None).result(timeout=10)
    if not service.runtime.wait_for_idle(timeout=10):
        raise AssertionError('Synthetic fixture work did not become idle')
    service.executor.submit(lambda: None).result(timeout=10)


class SyntheticProvider:
    """Explicitly non-native provider: deterministic static fixture evidence, no engine process."""
    def __init__(self, identifier, mode, asset):
        """Pin one owned marker file so real runtime asset/provenance fences still execute."""
        self.provider_id, self.mode, self.asset = identifier, mode, asset
        self.probes, self.analyses = 0, 0

    def probe(self, context, cancel):
        """Return controlled missing/available outcomes without inspecting host installations."""
        self.probes += 1
        if self.mode == 'missing_engine':
            return ProbeOutcome(SemanticAvailability('missing', 'benchmark-synthetic-no-engine'))
        return ProbeOutcome(SemanticAvailability('available', 'benchmark-synthetic-not-native',
            engine_fingerprint=sha256(self.asset.read_bytes()).hexdigest(), capabilities=('definition',)), (self.asset,))

    def analyze(self, context, probe, request, cancel):
        """Return exactly the source-anchored call in the two-file fixture; not native qualification."""
        self.analyses += 1
        source = SemanticSymbol('synthetic-run', 'method', 'run', SourceSpan('a.py', 2, 0, 3, 17))
        target = SemanticSymbol('synthetic-work', 'method', 'work', SourceSpan('b.py', 0, 4, 0, 8))
        fact = SemanticFact('call', source, target, 'confirmed-static', SourceSpan('a.py', 3, 11, 3, 17))
        return SemanticResult(context, self.provider_id, context.provider_fingerprint,
            probe.availability.engine_fingerprint, 'partial', (fact,),
            {'definition': SemanticCoverage('partial', 1, 1, ('synthetic-not-native',))})


@dataclass
class Case:
    """Own all temporary files, store/runtime handles and counters for a single measured case."""
    mode: str
    state: object
    snapshot: Path
    graph_hash: str
    providers: list
    service: object = None

    def close(self):
        """Close the coordinator before its store; never leave harness work renewing in the background."""
        if self.service is not None:
            self.service.close()
        self.state.close()


def create_case(directory, mode, auxiliary_nodes):
    """Prepare equal graph bytes and isolated stores, then warm the real get_graph path."""
    root, data = directory / 'source', directory / 'state'
    root.mkdir(parents=True)
    hashes = {}
    for filename, content in SOURCES.items():
        (root / filename).write_bytes(content)
        hashes[filename] = sha256(content).hexdigest()
    nodes = []
    for name, filename, line in (('run', 'a.py', 3), ('work', 'b.py', 1)):
        nodes.append({'id': name, 'label': name + '()', 'kind': 'method', 'file': filename,
            'line': line, 'codeProjectId': 'fixture-python',
            'memberFacts': {'name': name, 'language': 'python'},
            'commentSource': {'sourceKind': 'snapshot-captured-source', 'sourceHash': hashes[filename]}})
    # Extra nodes model decoded-map size, not additional source or semantic work.
    nodes.extend({'id': f'aux-{index}', 'label': f'Auxiliary {index}', 'kind': 'external'}
                 for index in range(auxiliary_nodes))
    graph = {'nodes': nodes, 'edges': [], 'metadata': {'snapshotId': 'snapshot'}}
    snapshot = data / 'fixture' / 'snapshot' / 'atlas-graph.json'
    store_module.write_json(snapshot, graph)
    state = store_module.ProjectStore(data)
    state.projects['fixture'] = {'id': 'fixture', 'name': 'Synthetic performance fixture',
        'path': str(root), 'status': 'completed', '_graph': str(snapshot), '_rawGraph': str(snapshot)}
    store_module.write_json(state.registry, state.projects)
    case = Case(mode, state, snapshot, sha256(snapshot.read_bytes()).hexdigest(), [])
    try:
        if mode != 'baseline':
            asset = directory / 'synthetic-engine-marker'
            asset.write_text('Synthetic fixture, not a native executable.\n')

            def factory(declaration, paths):
                """Ignore local installations; preserve application-owned profile/rule selection."""
                provider = SyntheticProvider(declaration['modId'] + '/' + declaration['id'], mode, asset)
                case.providers.append(provider)
                return provider

            case.service = SemanticEnrichment(state, provider_factory=factory)
            state.on_map_used = case.service.use
        state.get_graph('fixture')
        drain(case.service)
        if mode == 'applied_synthetic_overlay':
            case.service.use('fixture', 'snapshot', ['run'])
            drain(case.service)
        effective = state.get_graph('fixture')
        drain(case.service)
        if mode == 'applied_synthetic_overlay':
            if not any(edge['source'] == 'run' and edge['target'] == 'work' for edge in effective['edges']):
                raise AssertionError('Synthetic source-anchored overlay was not applied')
        elif effective['edges']:
            raise AssertionError('Baseline/missing mode acquired a synthetic edge')
        if mode == 'missing_engine' and not any(provider.probes for provider in case.providers):
            raise AssertionError('Missing-engine discovery was not exercised')
        for _ in range(3):
            state.get_graph('fixture')
        drain(case.service)
        return case
    except BaseException:
        case.close()
        raise


def process_observation(process):
    """Read current RSS and current owned descendants; do not call endpoint samples a peak."""
    observation = {'current_rss_bytes': process.memory_info().rss}
    try:
        observation['current_owned_child_count'] = len(process.children(recursive=True))
        observation['child_observation_status'] = 'observed'
    except (psutil.AccessDenied, PermissionError, psutil.NoSuchProcess) as error:
        observation['current_owned_child_count'] = None
        observation['child_observation_status'] = type(error).__name__
    return observation


def cache_observation(case):
    """Count only small private fixture caches; graph/source bytes are reported separately."""
    directory = case.snapshot.parent
    runtime = case.service.runtime if case.service else None
    return {'map_cache_entries': len(case.state._published_graph),
        'overlay_view_cache_entries': len(case.state.semantic_overlay.cache),
        'validated_delta_cache_entries': len(getattr(case.state.semantic_overlay, 'deltas', {})),
        'availability_cache_entries': len(runtime.records) if runtime else 0,
        'retained_result_entries': len(runtime.results) if runtime else 0,
        'pending_runtime_jobs': len(runtime.jobs) if runtime else 0,
        'map_state_entries': len(case.service.states) if case.service else 0,
        'availability_json_bytes': sum(path.stat().st_size for path in
            (runtime.directory.glob('*.json') if runtime else ())),
        'grouping_cache_bytes': sum(path.stat().st_size for path in directory.glob('atlas-grouped-*.json')),
        'overlay_bytes': (directory / 'semantic-overlay.json').stat().st_size
            if (directory / 'semantic-overlay.json').is_file() else 0,
        'status_bytes': (directory / 'semantic-status.json').stat().st_size
            if (directory / 'semantic-status.json').is_file() else 0}


@contextmanager
def forbid_warm_work():
    """Make unexpected warm AST parsing, source analysis and Python child launch observable even if swallowed."""
    calls = []

    def forbidden(*args, **kwargs):
        """Record before raising because background optional-runtime exceptions are intentionally contained."""
        calls.append(True)
        raise AssertionError('Warm path attempted parsing/analysis or child launch')

    with patch.object(method_flow_chunks, '_parse', forbidden), \
         patch.object(semantic_requests, 'parse_graph_source', forbidden), \
         patch.object(semantic_requests, 'declaration_bindings', forbidden), \
         patch.object(semantic_sources, 'parse_graph_source', forbidden), \
         patch.object(semantic_sources, 'declaration_bindings', forbidden), \
         patch.object(subprocess, 'Popen', forbidden):
        yield calls


def measure(case, iterations):
    """Measure warmed get_graph only; setup, draining, cache stats and process observations are outside timing."""
    drain(case.service)
    gc.collect()
    before_calls = [(provider.probes, provider.analyses) for provider in case.providers]
    process = psutil.Process()
    before = process_observation(process)
    durations = []
    with forbid_warm_work() as forbidden:
        cpu_start, wall_start = time.process_time_ns(), time.perf_counter_ns()
        for _ in range(iterations):
            start = time.perf_counter_ns()
            case.state.get_graph('fixture')
            durations.append((time.perf_counter_ns() - start) / 1_000_000)
        wall_ns = time.perf_counter_ns() - wall_start
        cpu_ns = time.process_time_ns() - cpu_start
        drain(case.service)
    after = process_observation(process)
    after_calls = [(provider.probes, provider.analyses) for provider in case.providers]
    if forbidden or before_calls != after_calls:
        raise AssertionError('Warm map use probed/analyzed, parsed source or launched a child')
    if case.graph_hash != sha256(case.snapshot.read_bytes()).hexdigest():
        raise AssertionError('Immutable graph bytes changed')
    return {'mode': case.mode, 'iterations': iterations,
        'median_ms': statistics.median(durations), 'p95_ms': percentile(durations, .95),
        'batch_wall_ms': wall_ns / 1_000_000, 'process_cpu_ms': cpu_ns / 1_000_000,
        'process_cpu_ms_per_read': cpu_ns / iterations / 1_000_000,
        'process_cpu_percent_of_wall': 100 * cpu_ns / wall_ns,
        'before': before, 'after': after,
        'current_rss_delta_bytes': after['current_rss_bytes'] - before['current_rss_bytes'],
        'warm_probe_calls': 0, 'warm_analyze_calls': 0, 'warm_ast_or_child_calls': 0,
        'cache': cache_observation(case), 'graph_sha256': case.graph_hash}


def run(repetitions, iterations, auxiliary_nodes):
    """Rotate three scenario orders across paired repetitions in one interpreter; report raw per-pair data."""
    runs, hashes = [], set()
    with tempfile.TemporaryDirectory(prefix='atlas-semantic-benchmark-') as temporary:
        base = Path(temporary)
        for repetition in range(repetitions):
            order = MODES[repetition % 3:] + MODES[:repetition % 3]
            for mode in order:
                directory = base / str(repetition) / mode
                case = create_case(directory, mode, auxiliary_nodes)
                try:
                    result = measure(case, iterations)
                    runs.append({'repetition': repetition + 1, 'order': list(order), **result})
                    hashes.add(case.graph_hash)
                finally:
                    case.close()
        if len(hashes) != 1:
            raise AssertionError('Paired scenarios did not use identical immutable graph bytes')
    summaries = {}
    for mode in MODES:
        selected = [item for item in runs if item['mode'] == mode]
        summaries[mode] = {'median_of_run_medians_ms': statistics.median(item['median_ms'] for item in selected),
            'median_cpu_ms_per_read': statistics.median(item['process_cpu_ms_per_read'] for item in selected),
            'max_observed_current_rss_bytes': max(item[point]['current_rss_bytes'] for item in selected for point in ('before', 'after')),
            'max_observed_owned_children': max((item[point]['current_owned_child_count'] for item in selected
                for point in ('before', 'after') if item[point]['current_owned_child_count'] is not None), default=None)}
    paired_deltas = []
    for repetition in range(1, repetitions + 1):
        pair = {item['mode']: item for item in runs if item['repetition'] == repetition}
        baseline = pair['baseline']
        for mode in MODES[1:]:
            measured = pair[mode]
            delta = measured['median_ms'] - baseline['median_ms']
            paired_deltas.append({'repetition': repetition, 'mode': mode,
                'median_latency_delta_ms': delta,
                'median_latency_delta_percent': 100 * delta / baseline['median_ms'],
                'cpu_delta_ms_per_read': measured['process_cpu_ms_per_read'] - baseline['process_cpu_ms_per_read']})
    return {'methodology': 'warm ProjectStore.get_graph; paired rotating order; synthetic non-native provider',
        'python': sys.version.split()[0], 'platform': platform.platform(),
        'repetitions': repetitions, 'iterations_per_case': iterations,
        'graph_nodes': auxiliary_nodes + 2, 'baseline_edges': 0, 'applied_edges': 1,
        'source_sha256': {name: sha256(content).hexdigest() for name, content in SOURCES.items()},
        'graph_sha256': next(iter(hashes)), 'native_engine_qualification': False,
        'summary': summaries, 'paired_deltas': paired_deltas, 'runs': runs,
        'limits': ['No HTTP serialization, browser rendering, extraction or native engine timing.',
                   'RSS and child counts are before/after current observations, not sampled peaks.',
                   'One interpreter retains allocator/import state; RSS is not per-engine incremental memory.',
                   'A complete small synthetic source cohort and auxiliary decoded nodes do not represent all project shapes.']}


def main():
    """Require at least three pairs and emit machine-readable measurements only after all assertions pass."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--auxiliary-nodes', type=int, default=1000)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 3 <= args.repetitions <= 30 or not 5 <= args.iterations <= 2000 or not 0 <= args.auxiliary_nodes <= 20000:
        parser.error('Use 3..30 repetitions, 5..2000 iterations and 0..20000 auxiliary nodes')
    payload = json.dumps(run(args.repetitions, args.iterations, args.auxiliary_nodes), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + '\n', encoding='utf-8')
    else:
        print(payload)


if __name__ == '__main__':
    main()
