"""Атрибуты закреплены в общей карте и обоих MCP-представлениях одной ревизии."""

import asyncio
from copy import deepcopy
import json
import pytest

from fastapi.testclient import TestClient

from atlas import store, enrichment_pipeline
from atlas.mcp_graph import build_mcp_graph
from atlas.mcp_bridge import describe_node, query_graph
from atlas.node_description import build_node_description
from atlas.server import create_app
from atlas.grouping import enrich_graph
from test_jobs import add_project, fake_build, wait_for_job


def attribute_snapshot(graph, root):
    """Создаёт фиксированные факты для проверки публикации, независимо от AST-сканера."""
    result = deepcopy(graph)
    attribute = {'id': 'attr:example', 'label': 'HttpPost("calculate")', 'name': 'HttpPost',
                 'kind': 'attribute', 'text': 'HttpPost("calculate")', 'scope': 'declaration',
                 'ownerNodeId': 'first', 'file': 'Controller.cs', 'line': 3, 'endLine': 3,
                 'sourceKind': 'snapshot-captured-source', 'sourceHash': 'captured-hash',
                 'arguments': [{'position': 1, 'expression': '"calculate"'}]}
    result['nodes'][0]['attributeFacts'] = {'status': 'parsed', 'kind': 'lexical-source',
        'sourceKind': 'snapshot-captured-source', 'truncated': False,
        'groups': [{'scope': 'declaration', 'attributes': [{**attribute, 'attributeNodeId': attribute['id']}]}]}
    result['nodes'].append(attribute)
    result['edges'].append({'source': 'first', 'target': attribute['id'], 'relation': 'has_attribute'})
    result['metadata']['attributeGraph'] = {'version': 'fixture', 'truncated': False}
    return result


def test_attributes_publish_atomically_and_survive_source_changes(tmp_path, monkeypatch):
    """Lazy MCP читает каноническую карту; ошибка производной не меняет факты и raw bytes."""
    def local_grouping(graph):
        """Publication is independent of the separately tested external Graphistry worker."""
        return enrich_graph(graph, provider_override=lambda topology: [set(topology.nodes)])

    monkeypatch.setattr(store, 'enrich_graph', local_grouping)
    project = tmp_path / 'repo'
    project.mkdir()
    source = project / 'Controller.cs'
    source.write_text('[HttpPost("calculate")] class Controller {}')
    monkeypatch.setattr(store, 'build_graph', fake_build)
    monkeypatch.setattr(enrichment_pipeline, 'enrich_attribute_graph', attribute_snapshot)
    data = tmp_path / 'state'
    with TestClient(create_app(data), base_url='http://127.0.0.1:8765') as client:
        project_id, job = add_project(client, project)
        assert job['status'] == 'completed'
        state = client.app.state.store
        graph = client.get(f'/api/projects/{project_id}/graph').json()
        token = graph['metadata']['snapshotId']
        mcp_path = state.mcp_snapshot(project_id, token, 'attr:example')
        raw_path = state.raw_graph(project_id)
        raw_bytes = raw_path.read_bytes()
        assert mcp_path != raw_path
        exported = json.loads(mcp_path.read_text())
        assert exported['graph']['metadata']['snapshotId'] == token
        assert {node['id'] for node in exported['nodes']} == {node['id'] for node in graph['nodes']}
        source.write_text('[Changed] class Controller {}')
        response = client.post(f'/api/projects/{project_id}/graph-node', json={'nodeId': 'attr:example', 'snapshotId': token})
        assert response.status_code == 200, response.text
        assert response.json()['node']['text'] == 'HttpPost("calculate")'
        assert client.post(f'/api/projects/{project_id}/graph-node', json={'nodeId': 'attr:example', 'snapshotId': 'old'}).status_code == 409
        assert client.post(f'/api/projects/{project_id}/graph-neighbors', json={'nodeId': 'first', 'snapshotId': token, 'direction': 'outgoing'}).json()['edges'][0]['relation'] == 'has_attribute'
        assert client.post(f'/api/projects/{project_id}/graph-neighbors', json={'nodeId': 'first', 'snapshotId': token, 'direction': 'invalid'}).status_code == 422

        def fail_export(graph):
            """Производная Graphify может отказать, не отменяя доступную каноническую карту."""
            raise RuntimeError('export failure')

        original_export = store.build_mcp_graph
        monkeypatch.setattr(store, 'build_mcp_graph', fail_export)
        response = client.post(f'/api/projects/{project_id}/rebuild', json={})
        published = wait_for_job(client, response.json()['job']['id'])
        assert published['status'] == 'completed'
        current_token = published['id']
        assert current_token != token
        with pytest.raises(store.StoreError) as error:
            state.mcp_snapshot(project_id, current_token)
        assert error.value.status == 503
        assert state.projects[project_id]['status'] == 'completed'
        assert client.get(f'/api/projects/{project_id}/graph').status_code == 200
        assert json.loads(mcp_path.read_text())['graph']['metadata']['snapshotId'] == token
        assert raw_path.read_bytes() == raw_bytes
        monkeypatch.setattr(store, 'build_mcp_graph', original_export)
        current_mcp = state.mcp_snapshot(project_id, current_token)
        assert current_mcp != mcp_path
    reopened = store.ProjectStore(data)
    try:
        _, frozen = reopened.comparison_snapshot(project_id)
        assert frozen['nodes'][0]['attributeFacts']['groups'][0]['attributes'][0]['name'] == 'HttpPost'
        assert reopened.mcp_snapshot(project_id, current_token) == current_mcp
        assert json.loads(current_mcp.read_text())['graph']['metadata']['snapshotId'] == current_token
        assert json.loads(mcp_path.read_text())['graph']['metadata']['snapshotId'] == token
    finally:
        reopened.close()


def test_mcp_export_preserves_parallel_relations_and_graphify_reads_attributes(tmp_path):
    """Настоящий Graphify MCP находит synthetic attribute и его исходного владельца."""
    from graphify.serve import _load_graph
    graph = attribute_snapshot({'nodes': [{'id': 'first', 'label': 'Controller', 'file': 'Controller.cs', 'line': 4}],
                                'edges': [], 'metadata': {}}, tmp_path)
    graph['edges'].append({'source': 'first', 'target': 'attr:example', 'relation': 'context'})
    graph['nodes'][0]['community'] = 'c-controller'
    graph['nodes'][1]['community'] = 'c-attributes'
    path = tmp_path / 'mcp-graph.json'
    store.write_json(path, build_mcp_graph(graph))
    loaded = _load_graph(str(path))
    assert loaded.number_of_edges('first', 'attr:example') == 2
    assert loaded.nodes['attr:example']['source_file'] == 'Controller.cs'
    assert isinstance(loaded.nodes['attr:example']['community'], int)
    assert loaded.nodes['attr:example']['atlasCommunityId'] == 'c-attributes'
    description = asyncio.run(describe_node(path, 'attr:example'))
    assert 'HttpPost' in description['text'] and 'calculate' in description['text'] and 'first' in description['text']
    answer = asyncio.run(query_graph(path, 'HttpPost'))
    # Graphify query_graph formats labels; exact raw-ID lookup is checked above
    # through the loaded graph and describe_node, independently of its text layout.
    assert 'HttpPost("calculate")' in answer['text'] and 'Controller' in answer['text']


def test_synthetic_description_uses_only_snapshot(tmp_path):
    """Удалённый рабочий исходник не уничтожает описание сохранённого атрибута."""
    graph = attribute_snapshot({'nodes': [{'id': 'first'}], 'edges': [], 'metadata': {}}, tmp_path)
    result = build_node_description(tmp_path, graph, 'attr:example', 'snap')
    assert result['sourceKind'] == 'snapshot-captured-source'
    assert result['capturedAttribute']['arguments'][0]['expression'] == '"calculate"'


def test_graphify_projection_distinguishes_unknown_from_empty(tmp_path):
    """Печатный Graphify get_node не выдаёт unavailable/partial за отсутствие атрибутов."""
    graph = {'nodes': [{'id': status, 'label': status, 'attributeFacts': {
        'status': status, 'groups': [], 'truncated': False,
    }} for status in ('unavailable', 'unresolved', 'partial', 'parsed')], 'edges': [], 'metadata': {}}
    projected = build_mcp_graph(graph)
    for node in projected['nodes']:
        attributes = node['attributes']
        assert attributes['atlas.attributesStatus'] == node['id']
        assert attributes['atlas.attributesTruncated'] == (node['id'] != 'parsed')
    path = tmp_path / 'mcp-graph.json'
    store.write_json(path, projected)
    for status in ('unavailable', 'parsed', 'partial'):
        response = asyncio.run(describe_node(path, status))
        assert f'"atlas.attributesStatus": "{status}"' in response['text']
