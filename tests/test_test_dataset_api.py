"""Dataset pages preserve declared evidence across UI/MCP, limits and snapshot changes."""
from copy import deepcopy
import json

import pytest

from atlas.graph_context import GraphContextError
from atlas.test_dataset_page import MAX_PAGE_BYTES, read_test_dataset_page
from atlas.test_containers import capture_test_containers
from atlas.test_entry_summary import build_test_entry_summary


def graph_fixture(count=30):
    """Build captured facts without an executable test project or provider."""
    rows = [{'id': f'row-{index}', 'index': index, 'file': 'Data.java', 'line': index + 1,
             'sourceNodeId': 'provider', 'bindings': [{'parameterId': 'p0', 'name': 'query', 'index': 0,
                 'value': {'kind': 'string', 'value': str(index), 'expression': f'"{index}"'},
                 'resolution': 'literal'}]} for index in range(count)]
    facts = {'id': 'test', 'label': 'Case', 'containerNodeId': 'class', 'runtimeStatus': 'unknown',
             'handlerNodeIds': ['method'], 'parameters': [{'id': 'p0', 'name': 'query', 'role': 'indexed'}],
             'dataSources': [{'id': 'source', 'nodeIds': ['provider'], 'status': 'declared',
                              'declaredRowCount': count + 1, 'rowsTruncated': True, 'rows': rows}]}
    return {'metadata': {'snapshotId': 'snap'}, 'nodes': [
        {'id': 'class'}, {'id': 'other-class'}, {'id': 'method', 'testFacts': facts}, {'id': 'provider'}]}


def test_pages_are_stable_detached_and_honest_about_capture_limits():
    graph = graph_fixture()
    original = deepcopy(graph)
    first = read_test_dataset_page(graph, 'method', 'snap', 'source', limit=20)
    second = read_test_dataset_page(graph, 'method', 'snap', 'source', first['nextCursor'])
    assert [row['id'] for row in first['rows'] + second['rows']] == [f'row-{i}' for i in range(30)]
    assert second['nextCursor'] is None and second['captureTruncated'] and second['truncated']
    first['rows'][0]['bindings'][0]['value']['value'] = 'changed'
    first['source']['nodeIds'].append('fake')
    assert graph == original
    catalogue = read_test_dataset_page(graph, 'method', 'snap')
    assert catalogue['rows'] == [] and 'rows' not in catalogue['sources'][0]
    assert catalogue['parameters'][0]['name'] == 'query'


@pytest.mark.parametrize('cursor', ['wrong', '!!!', '8J-YgA', ''])
def test_malformed_cursor_never_restarts_at_first_row(cursor):
    with pytest.raises(GraphContextError) as error:
        read_test_dataset_page(graph_fixture(), 'method', 'snap', 'source', cursor)
    assert error.value.status == 409


def test_cursor_cannot_cross_snapshot_test_or_source():
    graph = graph_fixture()
    cursor = read_test_dataset_page(graph, 'method', 'snap', 'source', limit=1)['nextCursor']
    graph['metadata']['snapshotId'] = 'new'
    with pytest.raises(GraphContextError):
        read_test_dataset_page(graph, 'method', 'new', 'source', cursor)
    graph['nodes'][2]['testFacts']['dataSources'][0]['id'] = 'different'
    with pytest.raises(GraphContextError):
        read_test_dataset_page(graph, 'method', 'new', 'different', cursor)


def test_utf8_budget_never_shortens_a_row_identity_or_advances_over_omitted_row():
    graph = graph_fixture(3)
    rows = graph['nodes'][2]['testFacts']['dataSources'][0]['rows']
    for row in rows:
        row['expression'] = 'Я' * 9000
    first = read_test_dataset_page(graph, 'method', 'snap', 'source')
    assert len(first['rows']) == 2 and first['nextCursor']
    assert len(json.dumps(first, ensure_ascii=False).encode()) < MAX_PAGE_BYTES
    second = read_test_dataset_page(graph, 'method', 'snap', 'source', first['nextCursor'])
    assert second['rows'][0]['id'] == 'row-2'
    rows[0]['expression'] = 'Я' * MAX_PAGE_BYTES
    with pytest.raises(GraphContextError) as error:
        read_test_dataset_page(graph, 'method', 'snap', 'source')
    assert error.value.status == 413


def test_container_uses_exact_owner_and_preview_excludes_rows():
    graph = graph_fixture()
    facts = graph['nodes'][2]['testFacts']
    capture_test_containers(graph['nodes'], [facts], {'status': 'partial'})
    container = graph['nodes'][0]['testContainerFacts']
    assert container['declaredTestCount'] == 1 and container['discoveryStatus'] == 'partial'
    assert 'testContainerFacts' not in graph['nodes'][1]
    summary = build_test_entry_summary(facts)
    assert summary['containerNodeId'] == 'class' and summary['dataSources'][0]['id'] == 'source'
    assert 'parameters' not in summary and 'rows' not in summary['dataSources'][0]


def test_api_uses_pinned_snapshot_reader_without_source_scan(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from atlas import server, store
    from test_grouping_cache import legacy_store
    state, project_id, path, graph = legacy_store(tmp_path)
    node_id = graph['nodes'][0]['id']
    graph['nodes'][0]['testFacts'] = graph_fixture()['nodes'][2]['testFacts']
    store.write_json(path, graph)
    state.close()
    def forbidden(*args):
        raise AssertionError('dataset endpoint must not clone graph or scan source')
    monkeypatch.setattr(store.ProjectStore, 'comparison_snapshot', forbidden)
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        response = client.get(f'/api/projects/{project_id}/test-datasets', params={
            'snapshotId': path.parent.name, 'nodeId': node_id, 'sourceId': 'source', 'limit': 2})
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page['rows']) == 2 and page['nextCursor']
        page['rows'][0]['bindings'].clear()
        again = client.get(f'/api/projects/{project_id}/test-datasets', params={
            'snapshotId': path.parent.name, 'nodeId': node_id, 'sourceId': 'source', 'limit': 2}).json()
        assert again['rows'][0]['bindings']
        assert client.get(f'/api/projects/{project_id}/test-datasets', params={
            'snapshotId': 'stale', 'nodeId': node_id}).status_code == 409
