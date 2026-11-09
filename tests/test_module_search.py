"""
Проверки M6 по контракту: независимые tiny oracles, ограничения, budgets и identity.
"""

from copy import deepcopy
from fractions import Fraction
from itertools import permutations
import json

import pytest

from atlas.module_analysis import project_modules
from atlas.module_analysis.models import digest
from atlas.module_search import ModuleSearchError, evaluate_partition, search_partitions


def projection(n=4, edges=None, *, level='class', partial=False):
    """
    Создаёт projection реальным Atlas-контрактом с явными directed фактами.
    """
    nodes = [{'id': str(i), 'kind': 'class', 'label': str(i), 'file': f'd{i}/a.py'} for i in range(n)]
    raw_edges = []
    for source, target in edges if edges is not None else [(0, 1), (1, 0), (2, 3), (3, 2)]:
        raw_edges.append({'source': str(source), 'target': str(target), 'relation': 'depends_on', 'confidence': 'EXTRACTED'})
    return project_modules({'nodes': nodes, 'edges': raw_edges, 'metadata': {'analysisStatus': 'partial' if partial else 'complete'}},
                           level=level, snapshot_id='frozen-snapshot')


def ids(graph):
    """
    Возвращает IDs в фиксированном порядке для независимых combinatorial fixtures.
    """
    return sorted(unit['id'] for unit in graph['units'])


def partitions(items):
    """
    Перечисляет все set partitions маленького списка без алгоритма поиска M6.
    """
    if not items:
        yield []
        return
    first, rest = items[0], items[1:]
    for partition in partitions(rest):
        yield [[first], *partition]
        for index in range(len(partition)):
            changed = deepcopy(partition)
            changed[index].append(first)
            yield changed


def matching_oracle(before, after):
    """
    Полный перебор group permutations даёт независимый move-count oracle.
    """
    size = max(len(before), len(after))
    a = [set(group) for group in before] + [set()] * (size - len(before))
    b = [set(group) for group in after] + [set()] * (size - len(after))
    best = max((sum(len(a[i] & b[j]) for i, j in enumerate(order)) for order in permutations(range(size))), default=0)
    return sum(map(len, before)) - best


def mq_oracle(graph, partition):
    """
    Вычисляет рациональную сумму MF через множества, отдельно от fitness engine.
    """
    result = Fraction(0)
    for group in map(set, partition):
        inside, incident = 0, 0
        for edge in graph['edges']:
            a, b = edge['source'], edge['target']
            if a == b:
                continue
            if a in group and b in group:
                inside += edge['count']
            elif (a in group) != (b in group):
                incident += edge['count']
        if inside:
            result += Fraction(2 * inside, 2 * inside + incident)
    return float(result)


def test_all_tiny_partitions_match_independent_objective_and_matching_oracles():
    graph = projection(5, [(0, 1), (0, 2), (2, 1), (3, 4), (1, 0), (4, 4)])
    units = ids(graph)
    current = [units[:2], units[2:4], units[4:]]
    candidates = list(partitions(units))
    assert len(candidates) == 52
    for partition in candidates:
        result = evaluate_partition(graph, partition, current)
        assert result['objectives']['atlasIncidentMQ'] == pytest.approx(mq_oracle(graph, partition))
        assert result['objectives']['movedUnits'] == matching_oracle(current, partition)
        assert result['source']['projectionHash'] == graph['projectionHash']


def test_matching_is_symmetric_and_label_order_invariant_for_all_four_unit_partitions():
    graph = projection(4, [])
    candidates = list(partitions(ids(graph)))
    assert len(candidates) == 15
    for before in candidates:
        for after in candidates:
            cost = evaluate_partition(graph, after, before)['objectives']['movedUnits']
            expected = matching_oracle(before, after)
            assert cost == expected
            renamed = [list(reversed(group)) for group in reversed(after)]
            assert evaluate_partition(graph, renamed, before)['objectives']['movedUnits'] == cost


def test_reproducible_tradeoff_and_exact_input_identity_without_mutation():
    graph = projection()
    units = ids(graph)
    current = [[units[0], units[2]], [units[1], units[3]]]
    before = deepcopy((graph, current))
    first = search_partitions(graph, current, seed=7, evaluations_per_method=64)
    second = search_partitions(graph, list(reversed(current)), seed=7, evaluations_per_method=64)
    assert first == second
    assert (graph, current) == before
    assert first['source']['sourceGraphHash'] == graph['sourceGraphHash']
    assert first['source']['snapshotId'] == 'frozen-snapshot'
    assert first['algorithm'] == 'atlas-pareto-ga/v1'
    assert len(first['proposals']) >= 2
    for proposal in first['proposals']:
        obj = proposal['objectives']
        assert obj['movedUnits'] == matching_oracle(current, proposal['partition'])
        assert obj['atlasIncidentMQ'] == pytest.approx(mq_oracle(graph, proposal['partition']))
        for other in first['proposals']:
            a = other['objectives']
            assert not (a['atlasIncidentMQ'] >= obj['atlasIncidentMQ'] and a['movedUnits'] <= obj['movedUnits']
                        and (a['atlasIncidentMQ'] > obj['atlasIncidentMQ'] or a['movedUnits'] < obj['movedUnits']))
    assert all(method['evaluations'] == 64 for method in first['methods'].values())
    assert first['baselines']['sarf']['status'] == 'unavailable'


@pytest.mark.parametrize('level', ['class', 'folder'])
def test_must_link_apart_and_size_limits_hold_in_every_proposal(level):
    graph = projection(5, [(0, 1), (1, 2), (3, 4)], level=level)
    a, b, c, d, e = ids(graph)
    current = [[a, b], [c, d, e]]
    constraints = {'mustLink': [[a, b]], 'cannotLink': [[a, c]],
                   'minGroupUnits': 2, 'maxGroupUnits': 3, 'minGroups': 2, 'maxGroups': 2}
    result = search_partitions(graph, current, constraints=constraints, evaluations_per_method=40)
    for proposal in result['proposals']:
        groups = proposal['partition']
        assert len(groups) == 2
        assert all(2 <= len(group) <= 3 for group in groups)
        assert any(a in group and b in group for group in groups)
        assert not any(a in group and c in group for group in groups)
    assert result['constraints'] == constraints
    assert result['source']['level'] == level


@pytest.mark.parametrize('case', ['contradiction', 'unknown', 'bad-size', 'unknown-key', 'current-infeasible'])
def test_invalid_constraints_never_get_silently_repaired(case):
    graph = projection(2, [(0, 1)])
    a, b = ids(graph)
    invalid = {'contradiction': {'mustLink': [[a, b]], 'cannotLink': [[a, b]]},
               'unknown': {'cannotLink': [[a, 'missing']]}, 'bad-size': {'maxGroupUnits': True},
               'unknown-key': {'maxSize': 1}, 'current-infeasible': {'mustLink': [[a, b]]}}
    with pytest.raises(ModuleSearchError):
        search_partitions(graph, [[a], [b]], constraints=invalid[case])


def test_duplicates_consume_equal_caps_and_rejected_attempts_are_bounded():
    graph = projection(2, [])
    a, b = ids(graph)
    result = search_partitions(graph, [[a, b]], constraints={'mustLink': [[a, b]]}, evaluations_per_method=7)
    assert all(item['evaluations'] == 7 for item in result['methods'].values())
    assert all(item['attempts'] == 7 for item in result['methods'].values())
    assert result['budgets']['baselineObjectiveEvaluations'] == 1
    constrained = search_partitions(graph, [[a], [b]], constraints={'cannotLink': [[a, b]], 'minGroups': 2},
                                    evaluations_per_method=7)
    for method in constrained['methods'].values():
        assert method['attempts'] <= 28
        assert method['evaluations'] <= 7
        assert method['evaluations'] + method['rejected'] == method['attempts']


def test_cancel_before_search_and_during_evaluation_never_returns_partial_objectives():
    graph = projection()
    current = [[unit] for unit in ids(graph)]
    result = search_partitions(graph, current, cancelled=lambda: True)
    assert result['status'] == 'cancelled' and result['proposals'] == []
    assert result['methods'] == {} and result['budgets']['baselineObjectiveEvaluations'] == 0
    count = 0

    def cancel():
        """
        Отменяет во время bounded вычисления, а не при создании input.
        """
        nonlocal count
        count += 1
        return count > 35

    result = search_partitions(graph, current, evaluations_per_method=256, cancelled=cancel)
    assert result['status'] == 'cancelled'
    assert sum(item['evaluations'] for item in result['methods'].values()) < 3 * 256
    assert all(item['stopReason'] == 'cancelled' for item in result['methods'].values())
    assert all('movedUnits' in proposal['objectives'] for proposal in result['proposals'])


@pytest.mark.parametrize('argument,value', [('seed', True), ('evaluations_per_method', 0),
                                         ('evaluations_per_method', 257), ('max_proposals', 17), ('cancelled', True)])
def test_invalid_search_parameters_rejected(argument, value):
    graph = projection(1, [])
    with pytest.raises(ModuleSearchError):
        search_partitions(graph, [ids(graph)], **{argument: value})


def test_projection_identity_budget_and_explicit_partition_validation():
    graph = projection(2, [])
    units = ids(graph)
    changed = deepcopy(graph)
    changed['edges'].append({'source': units[0], 'target': units[1], 'count': 1})
    with pytest.raises(ModuleSearchError, match='hash'):
        search_partitions(changed, [units])
    with pytest.raises(ModuleSearchError, match='explicit'):
        search_partitions(graph, None)
    with pytest.raises(ModuleSearchError, match='repeated'):
        search_partitions(graph, [units, units])
    oversized = projection(129, [])
    with pytest.raises(ModuleSearchError) as error:
        search_partitions(oversized, [ids(oversized)])
    assert error.value.status == 413


def test_external_baselines_are_observed_not_free_seeds_and_infeasible_is_explicit():
    graph = projection(3, [(0, 1)])
    units = ids(graph)
    current = [[unit] for unit in units]
    result = search_partitions(graph, current, baselines={'sarf': [units], 'leiden': current}, evaluations_per_method=2)
    assert result['budgets']['baselineObjectiveEvaluations'] == 3
    assert 'not-measured' in result['baselines']['sarf']['upstreamGenerationCost']
    assert all(method['evaluations'] == 2 for method in result['methods'].values())
    limited = search_partitions(graph, current, baselines={'sarf': [units]},
                                constraints={'maxGroupUnits': 2}, evaluations_per_method=2)
    assert limited['baselines']['sarf']['status'] == 'infeasible'


def test_partial_coverage_and_within_unit_loops_are_not_promoted_to_certainty():
    graph = projection(2, [(0, 0), (0, 1)], partial=True)
    units = ids(graph)
    result = search_partitions(graph, [units], evaluations_per_method=3)
    assert result['source']['coverage']['status'] == 'partial'
    obj = result['baselines']['current']['objectives']
    assert obj['withinUnitWeight'] == 1 and obj['atlasIncidentMQ'] == 1
    assert 'consumer-not-assessed' in result['qualification']


def test_empty_scope_is_explicit_and_isolates_stay_in_proposals():
    empty = search_partitions(projection(0, []), [], evaluations_per_method=2)
    assert empty['status'] == 'no-units' and empty['proposals'] == []
    graph = projection(4, [])
    result = search_partitions(graph, [[unit] for unit in ids(graph)], evaluations_per_method=4)
    assert all(sorted(sum(item['partition'], [])) == ids(graph) for item in result['proposals'])
    assert all(item['objectives']['atlasIncidentMQ'] == 0 for item in result['proposals'])


def test_output_limit_keeps_complete_partitions_and_reports_truncation():
    graph = projection()
    units = ids(graph)
    result = search_partitions(graph, [[units[0], units[2]], [units[1], units[3]]], max_proposals=1)
    assert len(result['proposals']) == 1 and result['truncated']
    assert sorted(sum(result['proposals'][0]['partition'], [])) == units
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 60_000


def test_reordered_capture_preserves_projection_and_search_result():
    raw = {'nodes': [{'id': str(i), 'kind': 'class', 'file': f'x{i}/x.py'} for i in range(4)],
           'edges': [{'source': '0', 'target': '1', 'relation': 'imports', 'confidence': 'EXTRACTED'}],
           'metadata': {'analysisStatus': 'complete'}}
    before = project_modules(raw, level='class', snapshot_id='s')
    raw['nodes'].reverse()
    raw['edges'].reverse()
    after = project_modules(raw, level='class', snapshot_id='s')
    current = [[unit] for unit in ids(before)]
    assert search_partitions(before, current, seed=17) == search_partitions(after, current, seed=17)
