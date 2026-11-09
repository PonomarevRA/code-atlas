"""Measure fixed Atlas analysis inputs without installing or running analyzed projects.

Run this same file against two source trees. Fixture hashes, source hashes, semantic
receipts and process costs make dropped work visible alongside timing changes.
"""

import argparse
import gc
import hashlib
import json
import platform
import statistics
import sys
import threading
import time
from pathlib import Path

import psutil


def digest(path):
    """Hash exact fixture bytes, independently of mutable names and Git revisions."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(root):
    """Fingerprint Python analysis code and policy bytes actually loaded by the run."""
    files = {}
    for directory, suffix in ((root / 'atlas', '*.py'), (root / 'resources/code-health', '*.json')):
        for path in sorted(directory.rglob(suffix)):
            files[path.relative_to(root).as_posix()] = digest(path)
    encoded = json.dumps(files, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_inputs(manifest_path):
    """Reject changed fixtures before importing either candidate implementation."""
    manifest = json.loads(manifest_path.read_text())
    for item in manifest['files']:
        path = manifest_path.parent / item['path']
        if digest(path) != item['sha256']:
            raise ValueError('Fixture hash mismatch: ' + item['path'])
    return manifest


def profile_operation(graph_path):
    """Evaluate captured dependency and organization facts without reparsing sources."""
    from atlas.code_health import dependency_profiles
    from atlas.code_organization import organization_profiles

    graph = json.loads(graph_path.read_text())
    node_count, edge_count = len(graph['nodes']), len(graph['edges'])

    def operation():
        """Return an accountable inventory instead of treating success as HTTP 200."""
        profiles = dependency_profiles(graph)
        organization = organization_profiles(graph['nodes'], graph['edges'], profiles)
        if len(profiles) != node_count:
            raise AssertionError('Dependency inventory changed')
        return {
            'nodes': node_count, 'edges': edge_count, 'profiles': len(profiles),
            'callTargets': sum(row['callTargets'] for row in profiles.values()),
            'knownDependencies': sum(row['status'] == 'complete' for row in profiles.values()),
            'unknownCalls': sum(row['unknownTargets'] or 0 for row in profiles.values()),
            'organizationFiles': len(organization),
            'knownOrganization': sum(row['score'] is not None for row in organization.values()),
        }

    return operation


def health_operation(source_path):
    """Parse a fixed C# corpus through the same captured-fact health entry point."""
    from atlas.code_health import enrich_code_health
    from atlas.code_health_source import parse_health_source

    content = source_path.read_bytes()

    def operation():
        """Include collection, policy and graph work; source is never executed."""
        parsed = parse_health_source('Cases.cs', content)
        if parsed['truncated'] or not parsed['methods']:
            raise AssertionError('Health corpus was skipped or truncated')
        graph = enrich_code_health({'nodes': parsed['methods'], 'edges': [], 'metadata': {}})
        rows = graph['nodes']
        return {
            'methods': len(rows),
            'statements': sum(row['complexity']['statementCount'] for row in rows),
            'calls': sum(len(row['healthCallFacts']['calls']) for row in rows),
            'knownHealth': sum(row['health']['score'] is not None for row in rows),
            'knownReadability': sum(row['health']['readabilityScore'] is not None for row in rows),
        }

    return operation


def flow_operation(root, node, shared):
    """Describe the exact captured TS declaration with cold or request-local reuse."""
    from atlas.execution_path_source import SourceAnalysis

    analysis = SourceAnalysis(root, 'agt116-fixture', 5, 1024 * 1024, 160) if shared else None

    def operation():
        """Account for source identity and emitted events even on an unavailable baseline."""
        current = analysis or SourceAnalysis(root, 'agt116-fixture', 5, 1024 * 1024, 160)
        description = current.describe(node)
        if description.get('file') != node['file']:
            raise AssertionError('Wrong source selected')
        flow = description.get('methodFlow') or {}
        return {
            'sourceStatus': description['sourceStatus'],
            'sourceHash': description.get('sourceHash'),
            'methodFlowStatus': flow.get('status'), 'truncated': flow.get('truncated'),
            'events': len(description.get('events', [])),
            'steps': len(flow.get('steps', [])),
            'eventNames': [event.get('callee') for event in description.get('events', [])],
        }

    return operation


def measure(operation, iterations):
    """Sample RSS independently; report fixture-loaded memory separately from phase peak."""
    process = psutil.Process()
    initial_rss = process.memory_info().rss
    peak = [initial_rss]
    stop = threading.Event()

    def sample():
        """Bound sampling lifetime to this operation phase, including failures."""
        while not stop.wait(0.01):
            peak[0] = max(peak[0], process.memory_info().rss)

    sampler = threading.Thread(target=sample, daemon=True)
    gc.collect()
    sampler.start()
    cpu_start = time.process_time()
    first_start = time.perf_counter()
    try:
        receipt = operation()
        first_ms = (time.perf_counter() - first_start) * 1000
        samples = []
        for _ in range(iterations):
            started = time.perf_counter()
            current = operation()
            samples.append((time.perf_counter() - started) * 1000)
            if current != receipt:
                raise AssertionError('Warm result differs from first semantic receipt')
    finally:
        stop.set()
        sampler.join()
        peak[0] = max(peak[0], process.memory_info().rss)
    ordered = sorted(samples)
    p95 = ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)] if len(ordered) >= 100 else None
    return {
        'iterations': iterations, 'firstMs': first_ms,
        'medianMs': statistics.median(samples), 'minMs': min(samples), 'maxMs': max(samples),
        'p95Ms': p95, 'cpuSeconds': time.process_time() - cpu_start,
        'phaseInitialRssBytes': initial_rss, 'sampledPeakRssBytes': peak[0],
        'semanticReceipt': receipt,
    }


def main():
    """Write one attributable receipt; callers orchestrate fresh paired processes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 1000:
        parser.error('iterations must be between 1 and 1000')
    source_root = args.source_root.resolve()
    manifest = load_inputs(args.fixtures)
    scenario = manifest['scenarios'][args.scenario]
    fixture_root = args.fixtures.parent
    sys.path.insert(0, str(source_root))
    if scenario['kind'] == 'profile':
        operation = profile_operation(fixture_root / scenario['file'])
    elif scenario['kind'] == 'health':
        operation = health_operation(fixture_root / scenario['file'])
    else:
        operation = flow_operation(fixture_root / scenario['root'], scenario['node'], scenario['shared'])
    result = {
        'schema': 'atlas-analysis-performance-v1', 'scenario': args.scenario,
        'fixtureManifestHash': digest(args.fixtures), 'harnessHash': digest(Path(__file__)),
        'sourceFingerprint': source_identity(source_root), 'python': sys.version,
        'platform': platform.platform(), 'samplingIntervalSeconds': 0.01,
        'scope': 'in-process backend; imports and fixture JSON load precede timed phase; no UI/HTTP timing',
        **measure(operation, args.iterations),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'scenario': args.scenario, 'output': str(args.output), 'medianMs': result['medianMs']}))


if __name__ == '__main__':
    main()
