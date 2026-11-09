"""Independent objective/aggregate oracles and public incremental pilot contracts."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import FrozenInstanceError
from itertools import combinations
import json
import random

import pytest

from atlas.module_updates import ModuleUpdateError, derive_delta, initial_state, update
from atlas.module_updates.hierarchy import aggregate, repair_aggregate
from atlas.module_updates.model import capture_projection, fingerprint, full_modularity


def projection(nodes, edges, snapshot='s1', **overrides):
    """Make a declared synthetic projection with the real version/hash contract."""
    value = {'version': 'module-analysis-v1', 'level': 'folder', 'snapshotId': snapshot,
             'sourceGraphHash': fingerprint({'nodes': nodes, 'edges': edges, 'snapshot': snapshot}),
             'scope': {'folders': None, 'ownershipSource': 'captured'},
             'coverage': {'status': 'complete'},
             'units': [{'id': node, 'sourceIds': ['raw:' + node], 'kind': 'folder'} for node in nodes],
             'edges': [{'source': a, 'target': b, 'relation': 'imports', 'count': w} for a, b, w in edges]}
    value.update(overrides)
    value['projectionHash'] = fingerprint(value)
    return value


def rehash(value):
    """Rehash intentional malformed/changed fixtures, without bypassing semantics."""
    value = deepcopy(value)
    value.pop('projectionHash', None)
    value['projectionHash'] = fingerprint(value)
    return value


def matrix_q(nodes, edges, groups):
    """Use the complete pair matrix null model, not production community sums."""
    adjacency = [[0 for _ in nodes] for _ in nodes]
    index = {node: i for i, node in enumerate(nodes)}
    for a, b, w in edges:
        if a != b:
            adjacency[index[a]][index[b]] += w
    mass = sum(sum(row) for row in adjacency)
    if not mass:
        return None
    incoming = [sum(row[j] for row in adjacency) for j in range(len(nodes))]
    outgoing = [sum(row) for row in adjacency]
    owners = {node: i for i, group in enumerate(groups) for node in group}
    score = 0
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if owners[a] == owners[b]:
                score += adjacency[i][j] / mass - outgoing[i] * incoming[j] / mass**2
    return score


def all_partitions(nodes):
    """Enumerate each set partition with a separate recursive insertion oracle."""
    if not nodes:
        yield []
        return
    first, *rest = nodes
    for partition in all_partitions(rest):
        yield [[first]] + deepcopy(partition)
        for index in range(len(partition)):
            result = deepcopy(partition)
            result[index].append(first)
            yield result


@pytest.mark.parametrize('seed', range(12))
def test_full_directed_q_matches_independent_matrix_every_partition(seed):
    rng, nodes = random.Random(seed), list('abcde')
    edges = [(a, b, rng.randint(1, 9)) for a in nodes for b in nodes if rng.random() < .3]
    graph = capture_projection(projection(nodes, edges))
    for groups in all_partitions(nodes):
        assert full_modularity(graph, groups) == pytest.approx(matrix_q(nodes, edges, groups))
    state = initial_state(projection(nodes, edges), max_evaluations=256)
    oracle = max(matrix_q(nodes, edges, groups) for groups in all_partitions(nodes))
    assert state.report['objective']['afterNewGraph'] == pytest.approx(matrix_q(nodes, edges, state.partition))
    assert state.report['objective']['afterNewGraph'] <= oracle + 1e-12


def test_target_ranking_constants_and_old_new_graphs_are_distinct():
    before = projection(list('ab'), [('a', 'b', 1)])
    old = initial_state(before)
    after = projection(list('ab'), [('a', 'b', 1), ('b', 'a', 1)], 's2')
    result = update(old, after, derive_delta(before, after)).report
    assert result['objective']['beforeOldGraph'] == 0
    assert result['objective']['beforeNewGraph'] == -.5
    assert result['objective']['afterNewGraph'] == 0
    assert result['delta']['globalWeightChanged'] is True
    assert result['delta']['globalFrontier'] is True


def independent_aggregate(edges, parents):
    """Rebuild by scanning every parent pair against all original edge summands."""
    result = {}
    labels = sorted(set(parents.values()))
    for a in labels:
        for b in labels:
            weight = sum(w for source, target, w in edges if parents[source] == a and parents[target] == b)
            if weight:
                result[a, b] = weight
    return result


@pytest.mark.parametrize('seed', range(12))
def test_subtract_add_against_independent_full_rebuild_with_parent_and_weight_changes(seed):
    rng, nodes = random.Random(seed), list('abcde')
    old_edges = tuple((a, b, rng.randint(1, 9)) for a in nodes for b in nodes if rng.random() < .3)
    new_edges = tuple((a, b, rng.randint(1, 9)) for a in nodes for b in nodes if rng.random() < .3)
    old_parents = {node: 'old' + str(rng.randrange(3)) for node in nodes}
    new_parents = {node: 'new' + str(rng.randrange(3)) for node in nodes}
    repaired, evidence = repair_aggregate(old_edges, new_edges, old_parents, new_parents,
                                          aggregate(old_edges, old_parents))
    assert {(a, b): w for a, b, w in repaired} == independent_aggregate(new_edges, new_parents)
    assert evidence['fullRebuildVerified'] is True


def test_refined_parents_are_separate_from_logical_labels_and_include_self_aggregate_weights():
    value = projection(list('abc'), [('a', 'b', 4), ('b', 'a', 4), ('b', 'c', 1)])
    state = initial_state(value, [list('abc')], max_evaluations=1)
    assert len(state.refined) >= len(state.partition)
    assert len(set(dict(state.parents).values())) == len(state.refined)
    assert sum(w for _, _, w in state.aggregate_edges) == 9
    assert state.report['search']['evaluationsUsed'] == 1
    assert state.report['search']['stopReason'] == 'evaluation-budget'
    assert sorted(node for group in state.partition for node in group) == list('abc')


def test_changing_both_parents_never_subtracts_the_same_directed_edge_twice():
    before = (('a','a',1), ('a','b',3), ('b','a',2))
    after = (('a','a',1), ('a','b',4))
    old_parents, new_parents = {'a':'x','b':'y'}, {'a':'z','b':'z'}
    result, _ = repair_aggregate(before, after, old_parents, new_parents, aggregate(before, old_parents))
    assert result == (('z','z',5),)


def test_bridge_deletion_can_split_a_previous_group_via_refined_aggregate_blocks():
    nodes = list('abcd')
    edges = [('a', 'b', 9), ('b', 'a', 9), ('c', 'd', 9), ('d', 'c', 9)]
    before = projection(nodes, [('a', 'b', 1), ('b', 'c', 1), ('c', 'd', 1), ('d', 'a', 1)])
    # A bounded seed can deliberately retain a coarse group to exercise splitting.
    state = initial_state(before, [nodes], max_evaluations=1)
    after = projection(nodes, edges, 's2')
    result = update(state, after, derive_delta(before, after), max_evaluations=4096)
    assert {frozenset(g) for g in result.state.partition} == {frozenset('ab'), frozenset('cd')}
    assert result.report['objective']['afterNewGraph'] == pytest.approx(.5)


def test_local_equal_mass_update_preserves_unaffected_parents_without_global_frontier_claim():
    nodes = list('abcdef')
    edges = [('a', 'b', 2), ('b', 'a', 2), ('c', 'd', 3), ('d', 'c', 3), ('e', 'f', 4), ('f', 'e', 4)]
    before = projection(nodes, edges)
    previous = initial_state(before)
    after = projection(nodes, [('a', 'b', 3), ('b', 'a', 1)] + edges[2:], 's2')
    result = update(previous, after, derive_delta(before, after))
    assert result.report['delta']['globalWeightChanged'] is False
    assert result.report['delta']['initialFrontierUnits'] == 2
    assert result.report['search']['visitedGroundUnits'] == 2
    for node in 'cdef':
        assert dict(result.state.parents)[node] == dict(previous.parents)[node]


@pytest.mark.parametrize('mode', ['unknown', 'missing-edge', 'wrong-identity', 'wrong-schema', 'invalid'])
def test_untrusted_delta_cannot_silently_enable_incremental_reuse(mode):
    before = projection(list('ab'), [('a', 'b', 1)])
    after = projection(list('ab'), [('a', 'b', 2)], 's2')
    delta = derive_delta(before, after)
    if mode == 'unknown': delta = None
    if mode == 'missing-edge': delta['edgeChanges'] = []
    if mode == 'wrong-identity': delta['fromSnapshotId'] = 'other'
    if mode == 'wrong-schema': delta['version'] = 'other'
    if mode == 'invalid': delta = ['not', 'a', 'delta']
    result = update(initial_state(before), after, delta)
    fresh = initial_state(after)
    assert result.state.partition == fresh.partition
    assert result.report['delta']['status'] == 'full-refresh'
    assert result.report['delta']['fallbackReasons']


@pytest.mark.parametrize('change', ['add', 'remove', 'ownership', 'profile', 'coverage'])
def test_incompatible_projection_changes_fallback_explicitly(change):
    before = projection(list('abc'), [('a', 'b', 1), ('b', 'c', 1)])
    if change == 'add': after = projection(list('abcd'), [('a', 'b', 1)], 's2')
    elif change == 'remove': after = projection(list('ab'), [('a', 'b', 1)], 's2')
    else:
        after = deepcopy(before)
        after['snapshotId'] = 's2'
        if change == 'ownership': after['units'][0]['sourceIds'] = ['new:raw']
        if change == 'profile': after['scope']['folders'] = ['scope']
        if change == 'coverage': after['coverage']['status'] = 'partial'
        after = rehash(after)
    result = update(initial_state(before), after, derive_delta(before, after))
    assert result.report['delta']['status'] == 'full-refresh'
    assert set(result.report['membership']['addedUnits']) == (set(after['units'][i]['id'] for i in range(len(after['units']))) - set('abc'))
    assert sorted(node for group in result.state.partition for node in group) == sorted(unit['id'] for unit in after['units'])


def test_zero_edges_empty_graph_and_isolates_keep_explicit_undefined_objective():
    for nodes in ([], ['a'], list('abc')):
        state = initial_state(projection(nodes, []))
        assert state.report['status'] == 'no-edges'
        assert state.report['objective']['afterNewGraph'] is None
        assert state.partition == tuple((node,) for node in nodes)


def test_typed_multiplicity_direction_and_unit_self_dependencies_are_explicit():
    value = projection(list('ab'), [('a', 'b', 2), ('a', 'b', 3), ('b', 'a', 1), ('a', 'a', 999)])
    state = initial_state(value)
    assert state.graph.edges == (('a', 'b', 5), ('b', 'a', 1))
    assert state.report['objective']['newWeight'] == 6
    assert state.report['objective']['afterNewGraph'] == pytest.approx(matrix_q(list('ab'), [('a','b',5),('b','a',1)], state.partition))


def test_state_reports_inputs_and_returned_json_are_independent_and_deterministic():
    value = projection(list('abc'), [('a', 'b', 2), ('b', 'a', 2)])
    original = deepcopy(value)
    state = initial_state(value)
    assert value == original
    with pytest.raises(FrozenInstanceError): state.graph.snapshot_id = 'changed'
    view = state.to_dict();view['partition'][0].append('invented');view['report']['groups'].clear()
    assert state.to_dict() != view and state.report['groups']
    shuffled = deepcopy(value);shuffled['units'].reverse();shuffled['edges'].reverse();shuffled = rehash(shuffled)
    again = initial_state(shuffled)
    assert state.partition == again.partition
    assert state.aggregate_edges == again.aggregate_edges
    empty_delta = derive_delta(value, value)
    repeated = update(state, value, empty_delta)
    assert repeated.state.partition == state.partition
    assert repeated.report['search']['evaluationsUsed'] == 0


@pytest.mark.parametrize('limit', [1, 2, 5, 17, 64])
def test_every_candidate_stage_respects_one_shared_objective_budget(limit):
    nodes = list('abcde')
    value = projection(nodes, [(a, b, 2) for a, b in combinations(nodes, 2)])
    state = initial_state(value, [nodes], max_evaluations=limit)
    search = state.report['search']
    assert search['evaluationsUsed'] == sum(search['stages'].values()) <= limit
    assert state.report['objective']['afterNewGraph'] >= matrix_q(nodes, [(a,b,2) for a,b in combinations(nodes,2)], [nodes]) - 1e-12


@pytest.mark.parametrize('invalid', [None, {}, [], True, 'projection'])
def test_invalid_projection_container(invalid):
    with pytest.raises(ModuleUpdateError): initial_state(invalid)


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown-edge', 'negative', 'float', 'boolean', 'containment', 'relation-object', 'level-array', 'coverage-array', 'unit-kind', 'ownership-overlap', 'missing-hash'])
def test_malformed_semantics_rejected_even_with_matching_content_hash(mutation):
    value = projection(list('ab'), [('a', 'b', 1)])
    if mutation == 'duplicate': value['units'].append(deepcopy(value['units'][0]))
    if mutation == 'unknown-edge': value['edges'][0]['target'] = 'unknown'
    if mutation == 'negative': value['edges'][0]['count'] = -1
    if mutation == 'float': value['edges'][0]['count'] = 1.2
    if mutation == 'boolean': value['edges'][0]['count'] = True
    if mutation == 'containment': value['edges'][0]['relation'] = 'contains'
    if mutation == 'relation-object': value['edges'][0]['relation'] = {}
    if mutation == 'level-array': value['level'] = ['folder']
    if mutation == 'coverage-array': value['coverage']['status'] = ['complete']
    if mutation == 'unit-kind': value['units'][0]['kind'] = 'function'
    if mutation == 'ownership-overlap': value['units'][1]['sourceIds'] = value['units'][0]['sourceIds']
    if mutation == 'missing-hash': value['sourceGraphHash'] = 'not-a-hash'
    with pytest.raises(ModuleUpdateError): initial_state(rehash(value))


@pytest.mark.parametrize('limit', [0, -1, True, 1.2, 16385, None])
def test_invalid_budget_and_partition_are_errors(limit):
    with pytest.raises(ModuleUpdateError): initial_state(projection(list('ab'), []), max_evaluations=limit)


@pytest.mark.parametrize('groups', [[['a']], [['a'], ['a','b']], [['a','b','z']], [[]]])
def test_partition_never_guesses_ownership(groups):
    with pytest.raises(ModuleUpdateError): initial_state(projection(list('ab'), []), groups)


def test_large_projection_and_forged_hash_fail_before_optimization():
    with pytest.raises(ModuleUpdateError) as error:
        initial_state(projection([str(i) for i in range(201)], []))
    assert error.value.status == 413
    value = projection(list('ab'), []);value['snapshotId'] = 'forged'
    with pytest.raises(ModuleUpdateError): initial_state(value)


def test_real_module_projection_contract_and_partial_coverage_are_preserved():
    from atlas.module_analysis import project_modules
    raw = {'nodes': [{'id':'a','kind':'function','file':'one/a.py'}, {'id':'b','kind':'function','file':'two/b.py'}],
           'edges':[{'source':'a','target':'b','relation':'imports'}]}
    captured = project_modules(raw, snapshot_id='source-snapshot')
    state = initial_state(captured)
    assert state.report['coverage'] == captured['coverage']
    assert state.report['snapshotId'] == 'source-snapshot'
    assert state.report['projectionHash'] == captured['projectionHash']
    assert state.report['status'] == 'partial'
    assert 'not a bound' in ' '.join(state.report['limitations'])


def test_class_granularity_uses_the_same_fixed_level_contract():
    value = projection(list('ab'), [('a','b',1),('b','a',1)])
    value['level'] = 'class'
    for unit in value['units']:
        unit['kind'] = 'class'
    state = initial_state(rehash(value))
    assert state.partition == (('a','b'),)
    assert state.report['objective']['afterNewGraph'] == 0
