"""Measure one cold canonical read; run in fresh processes for plain/packed pairs.

Only private temporary store state is created. Input bytes are read without an
overlay, so the decoded hash can prove complete equality across disk formats.
OS file caching is uncontrolled; this does not measure HTTP or native startup.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time

import psutil

from analysis_runner import digest, source_identity


def read_once(store, path):
    """Time physical JSON parsing/hydration, with a bounded RSS sampler lifetime."""
    process = psutil.Process()
    initial = process.memory_info().rss
    peak = [initial]
    stop = threading.Event()

    def sample():
        """Observe process RSS only during the physical read, including failures."""
        while not stop.wait(0.005):
            peak[0] = max(peak[0], process.memory_info().rss)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started, cpu = time.perf_counter(), time.process_time()
    try:
        graph = store._read_graph(path)
        wall_ms = (time.perf_counter() - started) * 1000
        cpu_seconds = time.process_time() - cpu
    finally:
        stop.set()
        sampler.join()
        peak[0] = max(peak[0], process.memory_info().rss)
    return graph, {
        'wallMs': wall_ms, 'cpuSeconds': cpu_seconds,
        'phaseInitialRssBytes': initial, 'sampledPeakRssBytes': peak[0],
    }


def main():
    """Verify exact input/source identities and save one attributable cold receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--graph', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph_path = args.graph.resolve()
    graph_hash = digest(graph_path)
    if graph_hash != args.expected_sha256:
        parser.error('graph hash does not match the frozen fixture')
    source_root = args.source_root.resolve()
    fingerprint = source_identity(source_root)
    sys.path.insert(0, str(source_root))
    from atlas.store import ProjectStore

    with tempfile.TemporaryDirectory(prefix='atlas-storage-measure-') as directory:
        store = ProjectStore(Path(directory))
        try:
            graph, measurement = read_once(store, graph_path)
        finally:
            store.close()
    # Full equality evidence is intentionally outside the measured phase/RSS.
    encoded = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    result = {
        'schema': 'atlas-storage-performance-v1', 'fixtureHash': graph_hash,
        'fixtureBytes': graph_path.stat().st_size, 'harnessHash': digest(Path(__file__)),
        'sourceFingerprint': fingerprint, 'python': sys.version, 'platform': platform.platform(),
        'scope': 'cold physical canonical read; no overlay, imports, hash, HTTP or UI in timed phase',
        'samplingIntervalSeconds': 0.005,
        'decodedGraphHash': hashlib.sha256(encoded).hexdigest(),
        'nodes': len(graph['nodes']), 'edges': len(graph['edges']), **measurement,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'wallMs': result['wallMs'],
                      'decodedGraphHash': result['decodedGraphHash']}))


if __name__ == '__main__':
    main()
