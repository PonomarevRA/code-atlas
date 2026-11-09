"""Source-only process replacement evidence; MCP/session proof belongs to integration."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from atlas.core_generations import CoreGenerations, build_bundle
from atlas.core_generations.artifacts import FILES
from atlas.module_analysis import project_modules


def compatible_source_copy(source_root, destination):
    """Build a trusted test N+1 with different actual code and unchanged computation.

    The extra implementation copies the two computed dictionaries after invoking
    the original real analysis. It does not substitute an echo or fake scorer.
    This source refactor is fixture evidence, not a released product upgrade.
    """
    destination = Path(destination)
    destination.mkdir()
    for name in FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(source_root) / name, target)
    with (destination / 'atlas/module_compute.py').open('a') as stream:
        stream.write("\n_previous_compute = compute_module_projection\n")
        stream.write("def compute_module_projection(projection, *, algorithms=('current','louvain','leiden'), seed=42, resolution=1.0):\n")
        stream.write("    result = _previous_compute(projection, algorithms=algorithms, seed=seed, resolution=resolution)\n")
        stream.write("    return {'assessment': dict(result['assessment']), 'comparison': dict(result['comparison'])}\n")
    return destination


def stable(result):
    """Remove only explicitly nondeterministic per-proposal elapsed wall time."""
    result = deepcopy(result)
    for item in result['comparison']['proposals']:
        item.pop('elapsedSeconds', None)
    return result


def probe():
    """Exercise actual pure code in two independently imported owned child processes."""
    captured = project_modules({'analysisStatus': 'complete', 'nodes': [
        {'id': 'a', 'file': 'first/a.py'}, {'id': 'b', 'file': 'second/b.py'}],
        'edges': [{'source': 'a', 'target': 'b', 'relation': 'imports'}]}, snapshot_id='synthetic-snapshot')
    with TemporaryDirectory(prefix='atlas-core-proof-') as directory:
        root = Path(directory)
        second_source = compatible_source_copy(ROOT, root / 'next-source')
        first = build_bundle(ROOT, root / 'N', generation_id='N')
        second = build_bundle(second_source, root / 'N1', generation_id='N1')
        supervisor = CoreGenerations()
        started = time.perf_counter()
        try:
            old_identity = supervisor.prepare(first)
            supervisor.activate('N')
            before = supervisor.compute(captured, algorithms=['current', 'louvain'])
            new_identity = supervisor.prepare(second)
            supervisor.activate('N1')
            after = supervisor.compute(captured, algorithms=['current', 'louvain'])
            assert stable(before) == stable(after)
            assert old_identity['pid'] != new_identity['pid']
            assert old_identity['computeHash'] != new_identity['computeHash']
            retired = supervisor.retire('N')
            assert retired['forced'] is False
        finally:
            supervisor.close()
        return {'schemaVersion': 'atlas-core-process-proof/v1', 'status': 'pass',
                'sourceKind': 'synthetic-captured-graph', 'old': old_identity, 'new': new_identity,
                'sameNonTimingResults': True, 'externalDependencyWeight': after['assessment']['metrics']['externalDependencyWeight'],
                'retired': retired, 'closed': supervisor.state(),
                'elapsedSeconds': time.perf_counter()-started,
                'timingKind': 'wall-clock-perf-counter',
                'timingScope': 'prepare/activate/compute both generations, retire old, close all',
                'limits': ['Projection compute processes only; stable app/state owner is not replaced.',
                           'Actual old MCP-client session proof is a separate integration test.',
                           'No native/frozen bundle, live user process, store migration or released upgrade qualification.',
                           'Hash identity is not publisher authentication; local source artifacts are trusted deployment inputs.']}


def main():
    """Print or save bounded reproducible source evidence in an explicit local path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = probe()
    encoded = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
