"""Description reads share immutable graph inputs and detach only their output."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from atlas import server, store
from atlas.graph_analysis_api import register_graph_analysis_routes
from test_control_flow import branch_project
from test_checkout_maps_api import private_api
from test_grouping_cache import legacy_store
from test_jobs import fake_build


def test_readonly_analysis_copies_only_result_and_detaches_nested_facts(tmp_path, monkeypatch):
    """A bounded response cannot mutate the shared node or registry metadata."""
    state, project_id, graph_path, graph = legacy_store(tmp_path)
    graph['nodes'][0]['health'] = {'items': [{'status': 'captured'}]}
    store.write_json(graph_path, graph)
    state.projects[project_id]['updatedAt'] = '2026-11-05T00:00:00Z'
    copied = []

    def bounded_copy(value):
        """Fail if the old whole-graph deepcopy returns on this hot path."""
        assert not (isinstance(value, dict) and 'nodes' in value and 'edges' in value)
        copied.append(value)
        return deepcopy(value)

    def read(root, snapshot):
        """Return nested shared facts so copy-out ownership is observable."""
        return {'health': snapshot['nodes'][0]['health'], 'metadata': snapshot['metadata']}

    monkeypatch.setattr(store, 'deepcopy', bounded_copy)
    try:
        first = state._read_snapshot_analysis(project_id, 'first', read)
        assert len(copied) == 1
        assert first['metadata']['graphUpdatedAt'] == '2026-11-05T00:00:00Z'
        first['health']['items'][0]['status'] = 'response only'
        first['metadata']['graphUpdatedAt'] = 'response only'
        second = state._read_snapshot_analysis(project_id, 'first', read)
        assert second['health']['items'][0]['status'] == 'captured'
        assert second['metadata']['graphUpdatedAt'] == '2026-11-05T00:00:00Z'
        assert state._published_graph[project_id][2]['metadata'].get('graphUpdatedAt') is None
    finally:
        state.close()


def test_readonly_analysis_releases_lock_and_rejects_concurrent_publication(tmp_path):
    """Publication proceeds during analysis and makes its old response fail with 409."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    entered, release, published = Event(), Event(), Event()
    observed = []
    newer_path = graph_path.parent.parent / 'newer' / graph_path.name
    newer = fake_build(tmp_path, newer_path.parent)
    newer['nodes'][0]['label'] = 'new revision'
    store.write_json(newer_path, newer)

    def read(root, graph):
        """Hold a live reference to the pinned old graph while the cache is replaced."""
        observed.append(graph['nodes'][0]['label'])
        entered.set()
        assert release.wait(5)
        assert graph['nodes'][0]['label'] == observed[0]
        return {'label': observed[0]}

    def publish():
        """Replace both the registry pointer and retained graph under the store lock."""
        with state.lock:
            state.projects[project_id]['_graph'] = str(newer_path)
            state._published_snapshot(project_id, newer_path)
        published.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            reader = workers.submit(state._read_snapshot_analysis, project_id, 'first', read)
            try:
                assert entered.wait(5)
                publisher = workers.submit(publish)
                assert published.wait(5)
            finally:
                release.set()
            publisher.result(timeout=5)
            with pytest.raises(store.StoreError) as stale:
                reader.result(timeout=5)
        assert stale.value.status == 409
        assert observed[0] != 'new revision'
        with pytest.raises(store.StoreError) as rejected:
            state._read_snapshot_analysis(project_id, 'first', read)
        assert rejected.value.status == 409
        assert len(observed) == 1
    finally:
        release.set()
        state.close()


def test_custom_description_builder_keeps_its_mutable_private_graph(tmp_path):
    """Default route registration preserves isolation for an arbitrary injected builder."""
    state, project_id, graph_path, graph = legacy_store(tmp_path)
    graph['nodes'][0]['nested'] = {'label': 'captured'}
    store.write_json(graph_path, graph)
    observed = []

    def mutable_builder(root, snapshot, node_id, snapshot_id):
        """Mutation is supported on the default custom-builder path only."""
        observed.append(snapshot['nodes'][0]['nested']['label'])
        snapshot['nodes'][0]['nested']['label'] = 'builder only'
        return {'nodeId': node_id, 'snapshotId': snapshot_id}

    app = FastAPI()
    register_graph_analysis_routes(app, state, description_builder=mutable_builder)
    try:
        with TestClient(app) as client:
            route = f'/api/projects/{project_id}/node-description'
            for _ in range(2):
                result = client.post(route, json={'nodeId': 'first', 'snapshotId': 'first'})
                assert result.status_code == 200
        assert observed == ['captured', 'captured']
    finally:
        state.close()


def test_production_description_route_never_copies_full_graph(tmp_path, branch_project, monkeypatch):
    """The normal composition root opts the audited description reader into shared input."""
    root, graph = branch_project
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    store.write_json(graph_path, graph)
    state.projects[project_id]['path'] = str(root)
    store.write_json(state.registry, state.projects)
    state.close()

    def bounded_copy(value):
        """The default production path must copy only the produced description."""
        assert not (isinstance(value, dict) and 'nodes' in value and 'edges' in value)
        return deepcopy(value)

    monkeypatch.setattr(store, 'deepcopy', bounded_copy)
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        response = client.post(f'/api/projects/{project_id}/node-description',
                               json={'snapshotId': 'first', 'nodeId': 'run'})
        assert response.status_code == 200, response.text
        assert response.json()['sourceStatus'] == 'parsed'
        assert response.json()['nodeId'] == 'run'


def test_checkout_description_route_uses_shared_input_before_renewing_ttl(private_api, monkeypatch):
    """The normal private-checkout reader benefits from copy-out and preserves useful-read TTL."""
    client, manager, clocks, _, _, session_id, snapshot_id = private_api
    base = '/api/checkout-sessions/' + session_id
    previous_expiry = client.get(base).json()['expiresAt']
    clocks.advance(20)

    def bounded_copy(value):
        """Description of one private node must not allocate a complete graph copy."""
        assert not (isinstance(value, dict) and 'nodes' in value and 'edges' in value)
        return deepcopy(value)

    monkeypatch.setattr(store, 'deepcopy', bounded_copy)
    response = client.post(base + '/describe', json={'snapshotId': snapshot_id, 'nodeId': 'first'})
    assert response.status_code == 200, response.text
    assert response.json()['checkoutSessionId'] == session_id
    assert response.json()['nodeId'] == 'first'
    assert client.get(base).json()['expiresAt'] != previous_expiry
