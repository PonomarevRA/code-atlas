"""Snapshot implementation names remain available outside the analysed path budget."""
import json
from atlas.implementation_catalog import implementation_catalog
from atlas.execution_path import build_execution_path
from tests.test_execution_path import method_project


def test_catalog_preserves_counts_names_and_bounds(monkeypatch):
    """Caps shorten summaries, not the actual count or raw graph facts."""
    nodes = {f'i{i}': {'id': f'i{i}', 'label': '.LoadAsync()', 'file': f'Loader{i}.cs', 'line': 9} for i in range(7)}
    edges = {'iface': [{'target': key, 'relation': 'dispatches_to'} for key in nodes]}
    before = json.dumps([nodes, edges], sort_keys=True)
    monkeypatch.setattr('atlas.implementation_catalog.MAX_IMPLEMENTATIONS', 3)
    result = implementation_catalog(nodes, edges)['iface']
    assert result['total'] == 7 and result['truncated']
    assert [item['label'] for item in result['items']] == [f'Loader{i}.LoadAsync()' for i in range(3)]
    assert not result['runtimeSelectionKnown']
    assert json.dumps([nodes, edges], sort_keys=True) == before


def test_delegate_and_test_sources_are_separate():
    """Forwarding to the same interface is an edge fact; test-folder candidates stay identifiable."""
    nodes = {'a': {'label': '.Load()', 'file': 'src/A.cs'}, 'trace': {'label': '.Load()', 'file': 'src/Trace.cs'},
             'test': {'label': '.Load()', 'file': 'tests/Fake.cs'}}
    dispatch = {'iface': [{'target': key, 'relation': 'dispatches_to'} for key in nodes]}
    calls = {'trace': [{'target': 'iface', 'relation': 'calls'}]}
    items = implementation_catalog(nodes, dispatch, calls)['iface']['items']
    assert {item['id']: item['scope'] for item in items} == {'a': 'source', 'trace': 'delegate', 'test': 'tests'}


def test_path_keeps_catalog_when_target_body_exceeds_budget(method_project):
    """A one-node path still tells which implementations exist for its unresolved interface call."""
    project, graph, _ = method_project
    graph['nodes'].extend({'id': f'impl{i}', 'label': '.Next()', 'kind': 'method', 'file': f'Impl{i}.cs', 'line': 1} for i in range(2))
    graph['edges'].extend({'source': 'next', 'target': f'impl{i}', 'relation': 'dispatches_to'} for i in range(2))
    result = build_execution_path(project, graph, 'start', 's', max_nodes=1)
    assert result['returnedNodes'] == 1 and result['truncation']['nodes']
    assert not result['edges']
    calls = [event for event in result['steps'][0]['events'] if event.get('callee') == 'Next']
    assert calls[0]['targetNodeIds'] == []
    assert calls[0]['implementationTargetIds'] == ['next']
    assert result['implementationCatalog']['next']['total'] == 2


def test_path_retains_known_call_navigation_without_visiting_body(method_project):
    """Body budgets must not turn a known private/static-like call into an unlinked source row."""
    project, graph, _ = method_project
    before = json.dumps(graph, sort_keys=True)
    result = build_execution_path(project, graph, 'start', 's', max_nodes=1)
    call = next(event for event in result['steps'][0]['events'] if event.get('callee') == 'Next')
    assert call['targetNodeIds'] == []
    assert call['navigationTargetIds'] == ['next']
    assert call['navigationTargetCount'] == 1
    assert not call['navigationTargetsTruncated']
    assert call['navigationConfidence'] in {'graph', 'candidate'}
    assert result['returnedNodes'] == 1 and result['truncation']['nodes']
    assert not result['edges']
    assert json.dumps(graph, sort_keys=True) == before
    unresolved = next(event for event in result['steps'][0]['events'] if event.get('callee') == 'StartActivity')
    assert not unresolved.get('navigationTargetIds'), 'No name-based invented target'


def test_navigation_references_remain_bounded(method_project, monkeypatch):
    """A truncated target list retains its real count without adding unvisited bodies."""
    project, graph, _ = method_project
    graph['nodes'].append({'id': 'next-copy', 'label': 'Next', 'kind': 'method', 'file': 'Service.cs', 'line': 13})
    graph['edges'].append({'source': 'start', 'target': 'next-copy', 'relation': 'calls'})
    monkeypatch.setattr('atlas.execution_path.MAX_NAVIGATION_TARGETS', 1)
    result = build_execution_path(project, graph, 'start', 's', max_nodes=1)
    call = next(event for event in result['steps'][0]['events'] if event.get('callee') == 'Next')
    assert len(call['navigationTargetIds']) == 1
    assert call['navigationTargetCount'] == 2 and call['navigationTargetsTruncated']
