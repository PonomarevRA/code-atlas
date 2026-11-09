"""Compare every frozen update with a fresh same-objective run and independent Q."""

import argparse
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from atlas.module_updates import derive_delta, initial_state, update
from atlas.module_updates.model import fingerprint


def matrix_objective(projection, groups):
    """Recompute Q over all ordered pairs, independently of production community sums."""
    nodes = [unit['id'] for unit in projection['units']]
    index = {node: i for i, node in enumerate(nodes)}
    weights = [[0] * len(nodes) for _ in nodes]
    for edge in projection['edges']:
        if edge['source'] != edge['target']:
            weights[index[edge['source']]][index[edge['target']]] += edge['count']
    total = sum(sum(row) for row in weights)
    if not total:
        return None
    outgoing = [sum(row) for row in weights]
    incoming = [sum(row[j] for row in weights) for j in range(len(nodes))]
    owner = {node: i for i, group in enumerate(groups) for node in group}
    score = 0.0
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if owner[a] == owner[b]:
                score += weights[i][j] / total - outgoing[i] * incoming[j] / total**2
    return score


def partitions(nodes):
    """Enumerate set partitions through recursive insertion for the small offline oracle."""
    if not nodes:
        yield []
        return
    first, *rest = nodes
    for prior in partitions(rest):
        yield [[first]] + [list(group) for group in prior]
        for index in range(len(prior)):
            candidate = [list(group) for group in prior]
            candidate[index].append(first)
            yield candidate


def exact_oracle(projection):
    """Maximize independently evaluated Q on at most seven units; no search sees the optimum."""
    nodes = sorted(unit['id'] for unit in projection['units'])
    if len(nodes) > 7:
        return {'status': 'unavailable', 'reason': 'exact oracle limited to seven units'}
    best, count = None, 0
    for candidate in partitions(nodes):
        value = matrix_objective(projection, candidate)
        if value is not None and (best is None or value > best):
            best = value
        count += 1
    return {'status': 'complete' if best is not None else 'no-edges', 'maxQ': best, 'partitionsEvaluated': count}


def check_state(state, projection):
    """Verify scored groups and every aggregate pair against independent ground summands."""
    expected_q = matrix_objective(projection, state.partition)
    actual_q = state.report['objective']['afterNewGraph']
    assert expected_q is None and actual_q is None or expected_q is not None and abs(expected_q - actual_q) < 1e-10
    parents, expected = dict(state.parents), {}
    for a in sorted(set(parents.values())):
        for b in sorted(set(parents.values())):
            value = sum(w for s, t, w in state.graph.edges if parents[s] == a and parents[t] == b)
            if value:
                expected[a, b] = value
    assert expected == {(a, b): w for a, b, w in state.aggregate_edges}
    report = state.report
    assert report['search']['evaluationsUsed'] == sum(report['search']['stages'].values()) <= report['search']['evaluationLimit']


def run_trace(trace, evaluation_limit):
    """Retain the actual prior result across updates, preserving all wins and losses."""
    projections = trace['projections']
    state = initial_state(projections[0], max_evaluations=evaluation_limit)
    check_state(state, projections[0])
    rows = []
    for step, mode in enumerate(trace['deltaModes'], start=1):
        previous, current = projections[step - 1], projections[step]
        delta = derive_delta(previous, current)
        if mode == 'unknown':
            delta = None
        elif mode == 'incomplete':
            delta['edgeChanges'] = []
        started = time.perf_counter()
        result = update(state, current, delta, max_evaluations=evaluation_limit)
        update_elapsed = time.perf_counter() - started
        started = time.perf_counter()
        fresh = initial_state(current, max_evaluations=evaluation_limit)
        fresh_elapsed = time.perf_counter() - started
        check_state(result.state, current)
        check_state(fresh, current)
        oracle = exact_oracle(current)
        observed, full_q = result.report['objective']['afterNewGraph'], fresh.report['objective']['afterNewGraph']
        gap = oracle.get('maxQ') - observed if oracle.get('maxQ') is not None and observed is not None else None
        rows.append({'step': step, 'deltaMode': mode, 'projectionHash': current['projectionHash'],
                     'incremental': result.report, 'fresh': fresh.report, 'exactOracle': oracle,
                     'gapToExact': gap, 'freshMinusIncrementalQ': full_q - observed if observed is not None and full_q is not None else None,
                     'timingKind': 'wall-clock-perf-counter',
                     'timingScope': 'separate complete update / initial_state calls including validation, full-Q and aggregate verification; delta derivation and independent oracles excluded',
                     'updateSeconds': update_elapsed, 'freshSeconds': fresh_elapsed,
                     'independentChecks': {'matrixQ': True, 'allAggregateWeights': True}})
        state = result.state
    return {'name': trace['name'], 'source': trace['source'], 'traceHash': fingerprint(trace), 'updates': rows}


def main():
    """Save bounded, inspectable evidence without executing or modifying source projects."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evaluations', type=int, default=128)
    args = parser.parse_args()
    fixture = Path(__file__).parent / 'fixtures' / 'traces.json'
    with fixture.open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        parser.error('Trace fixture exceeds 8 MiB')
    data = json.loads(raw)
    cases = [run_trace(trace, args.evaluations) for trace in data['traces']]
    losses = []
    for case in cases:
        for row in case['updates']:
            if row['freshMinusIncrementalQ'] is not None and row['freshMinusIncrementalQ'] > 1e-10:
                losses.append({'trace': case['name'], 'step': row['step'], 'freshMinusIncrementalQ': row['freshMinusIncrementalQ']})
    report = {'version': 'atlas-module-update-experiment/v1', 'algorithm': 'atlas-incremental-directed/v1',
              'paperReproduction': False, 'python': platform.python_version(), 'fixtureHash': fingerprint(data),
              'evaluationLimit': args.evaluations, 'cases': cases, 'lossesAgainstFresh': losses,
              'limitations': ['Eight small frozen traces, including a historical source base with hypothetical edits and the full seeded set7/42/91; no real commit stream or product benefit proof.',
                              'Fresh comparison is the same Atlas adaptation from singleton seeds, not Leiden or LD-Leiden.',
                              'Sequential lexicographic order; no random seed or parallel gain claim.',
                              'Every candidate in all three stages is budgeted; baseline/report Q and repair verification are additional work.',
                              'Composite elapsed wall-clock durations are not CPU/per-stage/native/UI/RSS/capacity measurements.',
                              'Partial coverage remains partial; no cross-snapshot Q monotonicity or semantic cohesion claim.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'traces': len(cases), 'updates': sum(len(case['updates']) for case in cases), 'losses': len(losses)}))


if __name__ == '__main__':
    main()
