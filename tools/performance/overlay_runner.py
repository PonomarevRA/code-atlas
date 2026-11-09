"""Measure snapshot-bound overlay reads against the same private captured graph.

Setup creates a minimal valid derivative outside the source tree. Cold samples
include JSON reading and hash validation; warm samples read the cached derivative.
This isolates the new integrity fence from health scoring and map construction.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from analysis_runner import digest, measure, source_identity


def main():
    """Compare identical snapshot bytes without opening a live application store."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--graph', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    from atlas import health_snapshot_overlay as overlays
    from atlas.code_health import ANALYZER_VERSION
    from atlas.code_health_policy import policy

    with tempfile.TemporaryDirectory(prefix='agt116-overlay-') as temporary:
        snapshot = Path(temporary) / 'snapshot' / 'atlas-graph.json'
        snapshot.parent.mkdir()
        shutil.copyfile(args.graph, snapshot)
        with snapshot.open('rb') as source:
            source_hash = hashlib.file_digest(source, 'sha256').hexdigest()
        derivative = {'snapshotId': 'snapshot', 'sourceStamp': list(overlays.file_stamp(snapshot)),
                      'sourceHash': source_hash, 'analyzerVersion': ANALYZER_VERSION,
                      'policyVersion': policy()['version'], 'nodes': {}, 'codeHealth': {}}
        snapshot.with_name('code-health-overlay.json').write_text(json.dumps(derivative))

        def cold():
            """Read and validate the published graph plus an uncached derivative."""
            overlays._read.cache_clear()
            if hasattr(overlays, 'snapshot_hash'):
                overlays.snapshot_hash.cache_clear()
            graph = json.loads(snapshot.read_text())
            if overlays.read_overlay(snapshot) != derivative:
                raise AssertionError('Overlay integrity check failed')
            return {'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
                    'sourceHash': source_hash, 'overlayAccepted': True}

        def warm():
            """Read only the cached derivative, as a retained published graph does."""
            result = overlays.read_overlay(snapshot)
            if result != derivative:
                raise AssertionError('Cached overlay differs')
            return {'sourceHash': source_hash, 'overlayAccepted': True}

        result = {'schema': 'atlas-overlay-performance-v1', 'fixtureHash': source_hash,
                  'harnessHash': digest(Path(__file__)),
                  'sourceFingerprint': source_identity(args.source_root),
                  'scope': 'minimal derivative; cold JSON+overlay, warm overlay only; OS cache uncontrolled',
                  'cold': measure(cold, 3), 'warm': measure(warm, 100)}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'coldMs': result['cold']['medianMs'],
                      'warmMs': result['warm']['medianMs']}))


if __name__ == '__main__':
    main()
