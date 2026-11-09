"""Real grammars and adversarial graph facts verify conservative call coverage."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from atlas.code_health import dependency_profiles, enrich_code_health
from atlas.code_health_calls import confirmed_call_edge, resolved_call_facts, unmatched_calls
from atlas.code_health_service import CodeHealthService
from atlas.code_health_source import parse_health_source


def method(source, filename='calls.cs', name='Run'):
    """Parse real source and select the requested local declaration."""
    return next(node for node in parse_health_source(filename, source.encode())['methods'] if node['name'] == name)


def graph_for(node, targets, edges):
    """Build an isolated source graph with explicit caller identity on every edge."""
    return {'nodes': [node, *targets], 'edges': [dict(edge, source=node['id'], relation='calls') for edge in edges], 'metadata': {}}


def test_call_spans_preserve_distinct_same_line_calls_and_utf8_bytes():
    source = 'class C { void Run(){ var label="цена"; a.Read(); b.Read(); } }'
    node = method(source)
    calls = node['healthCallFacts']['calls']
    assert [call['receiver'] for call in calls] == ['a', 'b']
    assert [source.encode()[call['span']['startByte']:call['span']['endByte']].decode() for call in calls] == ['a.Read()', 'b.Read()']
    targets = [{'id': 'a', 'label': 'A.Read()'}, {'id': 'b', 'label': 'B.Read()'}]
    line_edges = [{'target': 'a', 'line': 1}]
    graph = graph_for(node, targets, line_edges)
    assert len(unmatched_calls(node, graph['edges'], {n['id']: n for n in graph['nodes']})) == 2
    graph['edges'] = [dict(source=node['id'], target=target['id'], relation='calls', evidence={'callSite': call['span']})
                      for call, target in zip(calls, targets)]
    profile = dependency_profiles(graph)[node['id']]
    assert profile['unknownTargets'] == 0
    assert {call['target'] for call in profile['resolvedCalls']} == {'a', 'b'}


@pytest.mark.parametrize('expression', ['nameof(C)', '@nameof(C)', 'receiver.nameof(C)'])
def test_invocation_shaped_nameof_never_uses_name_as_semantic_proof(expression):
    source = 'class C { string Run(){ return ' + expression + '; } string @nameof(object x)=>"custom"; }'
    node = method(source)
    calls = node['healthCallFacts']['calls']
    assert len(calls) == 1
    assert calls[0]['resolution'] != 'confirmed'
    assert node['lexicalCalls'] == [expression.split('(')[0]]
    assert dependency_profiles(graph_for(node, [], []))[node['id']]['status'] == 'partial'
    if expression.startswith('nameof'):
        assert calls[0]['reason'] == 'contextual-keyword-binding-unavailable'
    else:
        assert calls[0]['category'] == 'unresolved'


@pytest.mark.parametrize('expression', ['items.Where(x => true)', 'items.ToList()', 'items.Contains(value)',
                                         'JsonSerializer.Deserialize<Data>(value)', 'global::System.Text.Json.JsonSerializer.Deserialize<Data>(value)',
                                         'Alias.Deserialize<Data>(value)'])
def test_bcl_names_qualified_names_and_aliases_are_not_external_proof(expression):
    node = method('using Alias = My.JsonSerializer; class C { object Run(){ return ' + expression + '; } }')
    graph = graph_for(node, [], [])
    profile = dependency_profiles(graph)[node['id']]
    assert profile['status'] == 'partial'
    assert profile['unknownTargets'] == 1
    assert profile['externalTargets'] == 0


def test_confirmed_external_graph_fact_sets_boundary_without_claiming_runtime():
    node = method('class C { object Run(){ return api.Read(); } }')
    external = {'id': 'library-read', 'kind': 'external', 'label': 'Library.Read()'}
    graph = graph_for(node, [external], [{'target': external['id'], 'evidence': {'span': node['healthCallFacts']['calls'][0]['span']}}])
    profile = dependency_profiles(graph)[node['id']]
    assert profile['status'] == 'complete'
    assert profile['externalTargets'] == 1
    assert profile['resolvedCalls'][0]['category'] == 'external-call'
    assert profile['runtimeSelectionKnown'] is False


@pytest.mark.parametrize('field,value', [('confidence', 'INFERRED'), ('resolutionStatus', 'ambiguous'), ('resolution', 'unknown')])
def test_uncertain_edges_do_not_resolve_or_create_recursion(field, value):
    node = method('class C { void Run(){ Run(); } }')
    graph = graph_for(node, [], [{'target': node['id'], 'line': 1, field: value}])
    profile = dependency_profiles(graph)[node['id']]
    assert profile['unknownTargets'] == 1
    assert profile['callTargets'] == 0
    assert profile['recursive'] is False
    assert confirmed_call_edge(graph['edges'][0]) is False


def test_overload_ambiguity_and_stale_source_hash_remain_unknown():
    node = method('class C { void Run(){ Read(1); } }')
    targets = [{'id': 'one', 'label': 'Read(int)'}, {'id': 'two', 'label': 'Read(string)'}]
    graph = graph_for(node, targets, [{'target': 'one', 'line': 1}, {'target': 'two', 'line': 1}])
    resolved = resolved_call_facts(node, graph['edges'], {n['id']: n for n in graph['nodes']})
    assert resolved[0]['resolution'] == 'ambiguous'
    graph['edges'] = [dict(graph['edges'][0], evidence={'sourceHash': 'stale'})]
    assert dependency_profiles(graph)[node['id']]['unknownTargets'] == 1


def test_owner_local_fallback_requires_unique_site_and_binding():
    node = method('class C { void Run(){ Read(); Read(); } }')
    node['memberFacts'] = {'ownerNodeId': 'C'}
    target = {'id': 'read', 'label': 'Read()', 'memberFacts': {'ownerNodeId': 'C', 'name': 'Read'}}
    graph = graph_for(node, [target], [{'target': 'read'}])
    assert dependency_profiles(graph)[node['id']]['unknownTargets'] == 2
    node = method('class C { void Run(){ Read(); } }')
    node['memberFacts'] = {'ownerNodeId': 'C'}
    graph = graph_for(node, [target], [{'target': 'read'}])
    assert dependency_profiles(graph)[node['id']]['unknownTargets'] == 0


def test_deferred_scopes_and_call_limit_retain_partial_coverage():
    node = method('class C { void Run(){ System.Action f = () => hidden(); void Local(){ nested(); } visible(); } }')
    assert node['lexicalCalls'] == ['visible']
    node = method('class C { void Run(){' + 'read();' * 201 + '} }')
    assert len(node['healthCallFacts']['calls']) == 200
    assert node['callsTruncated'] is True
    assert unmatched_calls(node, [], {}) is None


def test_snapshot_working_and_draft_share_collector_and_keep_unknown(tmp_path):
    content = b'class C { object Run(){ return items.Where(x => true).ToList(); } }'
    captured = parse_health_source('calls.cs', content)['methods']
    graph = enrich_code_health({'nodes': captured, 'edges': [], 'metadata': {}})
    service = CodeHealthService(SimpleNamespace(data_dir=tmp_path))
    working = service.evaluate_contents(deepcopy(graph), {'calls.cs': content})
    draft = service.evaluate_contents(deepcopy(graph), {'calls.cs': content}, source_kind='draft')
    for result in (working, draft):
        assert result['nodes'][0]['healthCallFacts'] == captured[0]['healthCallFacts']
        assert result['nodes'][0]['health']['dependencies']['unknownTargets'] == 2
        assert result['nodes'][0]['health']['score'] is None
        assert result['nodes'][0]['health']['componentCoverage']['flow']['status'] == 'complete'
        assert result['nodes'][0]['health']['componentCoverage']['dependencies']['status'] == 'unknown'


def test_changed_source_does_not_reuse_confirmed_snapshot_binding(tmp_path):
    original = b'class C { object Run(){ return api.Read(); } }'
    node = parse_health_source('calls.cs', original)['methods'][0]
    target = {'id': 'external', 'kind': 'external', 'label': 'Api.Read()'}
    graph = graph_for(node, [target], [{'target': 'external', 'line': 1}])
    service = CodeHealthService(SimpleNamespace(data_dir=tmp_path))
    before = service.evaluate_contents(graph, {'calls.cs': original})
    after = service.evaluate_contents(graph, {'calls.cs': original.replace(b'api.Read()', b'other.Read()')}, source_kind='draft')
    assert before['nodes'][0]['health']['dependencies']['status'] == 'complete'
    assert after['nodes'][0]['health']['dependencies']['unknownTargets'] == 1


def test_escaped_identifier_can_match_an_exact_graph_call_site():
    node = method('class C { void Run(){ @nameof(1); } void @nameof(int value){} }')
    target = {'id': 'user-nameof', 'memberFacts': {'name': '@nameof'}}
    graph = graph_for(node, [target], [{'target': target['id'], 'span': node['healthCallFacts']['calls'][0]['span']}])
    profile = dependency_profiles(graph)[node['id']]
    assert profile['unknownTargets'] == 0
    assert profile['resolvedCalls'][0]['category'] == 'project-call'


def test_matching_indexes_edges_once_instead_of_per_call():
    node = method('class C { void Run(){' + 'read();' * 200 + '} }')
    targets = [{'id': str(index), 'label': 'other' + str(index) + '()'} for index in range(1000)]
    edges = [{'target': target['id'], 'line': 1} for target in targets]

    class CountedNodes(dict):
        """Count target lookups to verify resolver work independently of wall-clock noise."""
        lookups = 0

        def get(self, key, default=None):
            """Record one graph target lookup."""
            self.lookups += 1
            return super().get(key, default)

    nodes = CountedNodes({target['id']: target for target in targets})
    assert len(unmatched_calls(node, edges, nodes)) == 200
    assert nodes.lookups == len(edges)


def test_digits_in_filename_are_not_a_call_line():
    node = method('class C { void Run(){ api.Read(); } }')
    target = {'id': 'external', 'kind': 'external', 'label': 'Api.Read()'}
    graph = graph_for(node, [target], [{'target': 'external', 'source_location': 'version1/File.cs'}])
    assert dependency_profiles(graph)[node['id']]['unknownTargets'] == 1


@pytest.mark.parametrize('uncertainty', [
    {'confidence': 'graph', 'graphConfidence': 'INFERRED'},
    {'confidence': 'EXTRACTED', 'evidence': {'confidence': 'candidate'}},
    {'confidence': 'graph', 'evidence': {'graphConfidence': 'LOW'}},
    {'confidence': 'LOW'},
])
def test_normalized_and_nested_uncertainty_does_not_become_confirmed(uncertainty):
    node = method('class C { void Run(){ Run(); } }')
    graph = graph_for(node, [], [dict(uncertainty, target=node['id'], line=1)])
    profile = dependency_profiles(graph)[node['id']]
    assert profile['callTargets'] == 0
    assert profile['unknownTargets'] == 1
    assert profile['recursive'] is False


def test_compact_health_explains_unknown_without_full_coverage_duplication():
    from atlas.code_health import compact_health

    node = method('class C { object Run(){ return api.Read(); } }')
    node = enrich_code_health(graph_for(node, [], []))['nodes'][0]
    compact = compact_health(node)['health']
    assert 'componentCoverage' not in compact
    assert 'organization' not in compact
    assert 'dependencies:missing-required-dependencies-facts' in compact['unknownReasons']
    assert not any(reason.startswith('flow:') or reason.startswith('size:') for reason in compact['unknownReasons'])


@pytest.mark.parametrize('self_call', [False, True])
@pytest.mark.parametrize('hash_location', ['sourceHash', 'evidence'])
def test_stale_edges_never_count_targets_or_create_recursion(self_call, hash_location):
    nodes = parse_health_source('calls.cs', b'class C { void A(){ B(); } void B(){ A(); } }')['methods']
    first, second = nodes
    if self_call:
        first['healthCallFacts']['calls'][0]['name'] = 'A'
        first['healthCallFacts']['calls'][0]['expression'] = 'A'
    pairs = [(first['id'], first['id'])] if self_call else [(first['id'], second['id']), (second['id'], first['id'])]
    stale = {'sourceHash': 'stale'} if hash_location == 'sourceHash' else {'evidence': {'sourceHash': 'stale'}}
    edges = [dict(source=source, target=target, relation='calls', line=1, **stale) for source, target in pairs]
    profiles = dependency_profiles({'nodes': nodes, 'edges': edges, 'metadata': {}})
    for source, _ in pairs:
        assert profiles[source]['callTargets'] == 0
        assert profiles[source]['unknownTargets'] == 1
        assert profiles[source]['recursive'] is False


def test_nested_generic_arguments_preserve_terminal_name_and_unknown_binding():
    node = method('class C { object Run(){ return JsonSerializer.Deserialize<Dictionary<string,List<int>>>(value); } }')
    call = node['healthCallFacts']['calls'][0]
    assert call['name'] == 'Deserialize'
    assert call['receiver'] == 'JsonSerializer'
    assert call['resolution'] == 'unknown'
    assert dependency_profiles(graph_for(node, [], []))[node['id']]['unknownTargets'] == 1


@pytest.mark.parametrize('source_kind', ['working-tree', 'draft'])
def test_comment_edit_reuses_binding_with_original_call_site_provenance(tmp_path, source_kind):
    """Old source coordinates remain explicitly bound to captured bytes after a comment shift."""
    import hashlib

    original = b'class C { object Run(){ api.Read(); return missing.Send(); } }'
    current = b'// inserted comment\n' + original
    node = parse_health_source('calls.cs', original, source_kind='snapshot-captured-source')['methods'][0]
    captured_facts = deepcopy(node['healthCallFacts'])
    target = {'id': 'external', 'kind': 'external', 'label': 'Api.Read()'}
    graph = graph_for(node, [target], [{'target': 'external', 'span': captured_facts['calls'][0]['span']}])
    captured_profile = dependency_profiles(graph)[node['id']]
    service = CodeHealthService(SimpleNamespace(data_dir=tmp_path))
    result = service.evaluate_contents(graph, {'calls.cs': current}, source_kind=source_kind)['nodes'][0]
    dependency = result['health']['dependencies']
    original_hash, current_hash = hashlib.sha256(original).hexdigest(), hashlib.sha256(current).hexdigest()
    assert result['health']['sourceHash'] == current_hash
    assert result['healthCallFacts']['calls'][0]['line'] == 2
    assert dependency['callTargets'] == captured_profile['callTargets'] == 1
    assert dependency['unknownTargets'] == captured_profile['unknownTargets'] == 1
    assert dependency['resolvedCalls'] == captured_profile['resolvedCalls']
    assert dependency['unresolvedCalls'] == captured_profile['unresolvedCalls']
    assert dependency['resolvedCalls'][0]['line'] == dependency['unresolvedCalls'][0]['line'] == 1
    assert dependency['callSiteProvenance']['sourceHash'] == original_hash != current_hash
    assert dependency['callSiteProvenance']['sourceKind'] == 'snapshot-captured-source'
    assert dependency['callSiteProvenance']['file'] == 'calls.cs'
    assert node['healthCallFacts'] == captured_facts


@pytest.mark.parametrize('edges,partial', [
    ([('a', 'b'), ('b', 'c')], set()),
    ([('a', 'b'), ('a', 'c'), ('b', 'd'), ('c', 'd')], set()),
    ([('a', 'b'), ('b', 'c'), ('c', 'b'), ('c', 'd')], set()),
    ([('a', 'a'), ('b', 'a'), ('c', 'b')], set()),
    ([('a', 'b'), ('b', 'a'), ('a', 'c'), ('b', 'c'), ('d', 'e')], {'e'}),
    ([('a', 'b'), ('a', 'c'), ('b', 'd'), ('c', 'd')], {'d'}),
    ([], {'isolated'}),
    ([], set()),
])
def test_component_path_propagation_matches_networkx_reference(edges, partial):
    """Chain, diamond, recursion and unknown propagation retain prior SCC semantics."""
    import networkx as nx
    from atlas.code_health import _apply_call_paths

    graph = nx.DiGraph(edges)
    graph.add_nodes_from(partial)
    components = list(nx.strongly_connected_components(graph))
    recursive = set()
    for members in components:
        if len(members) > 1 or any(graph.has_edge(member, member) for member in members):
            recursive.update(members)
    actual = {member: {'status': 'partial' if member in partial else 'complete'} for member in graph}
    expected = deepcopy(actual)
    condensed = nx.condensation(graph, components)
    depths, cycles, incomplete = {}, {}, {}
    for group in reversed(list(nx.topological_sort(condensed))):
        members = condensed.nodes[group]['members']
        children = list(condensed.successors(group))
        cycles[group] = bool(members & recursive) or any(cycles[child] for child in children)
        incomplete[group] = any(expected[member]['status'] != 'complete' for member in members) or any(incomplete[child] for child in children)
        depths[group] = 1 + max((depths[child] for child in children), default=0)
        for member in members:
            expected[member].update(maxKnownAcyclicCallDepth=depths[group], recursiveReachable=cycles[group],
                                   callPathStatus='partial' if incomplete[group] or cycles[group] else 'complete')
    _apply_call_paths(graph, components, recursive, actual)
    assert actual == expected
