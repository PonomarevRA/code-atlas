"""Contract tests for persisted overview and node paging across snapshot revisions."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from atlas.grouping import GROUPING_VERSION
from atlas.progressive_map import write_progressive_map
from atlas.progressive_map_api import register_progressive_map_routes
from atlas.store import ProjectStore, StoreError


def graph_fixture():
    """Return a grouped graph with project and cross-page edges."""
    return {
        'nodes': [
            {'id': 'a', 'label': 'A', 'kind': 'file', 'community': 'g', 'codeProjectId': 'p', 'attributeFacts': {'heavy': 1}},
            {'id': 'b', 'label': 'B', 'kind': 'symbol', 'community': 'g', 'codeProjectId': 'q'},
            {'id': 'c', 'label': 'C', 'kind': 'symbol', 'community': 'h', 'codeProjectId': 'p'},
        ],
        'edges': [{'source': 'a', 'target': 'b', 'relation': 'calls'}, {'source': 'a', 'target': 'c', 'relation': 'uses'}],
        'communities': [{'id': 'g', 'nodeCount': 2}, {'id': 'h', 'nodeCount': 1}],
        'communityEdges': [{'source': 'g', 'target': 'h', 'weight': 1}],
        'codeProjects': [{'id': 'p', 'nodeCount': 2}, {'id': 'q', 'nodeCount': 1}],
        'metadata': {
            'snapshotId': 'rev1', 'groupingVersion': GROUPING_VERSION, 'groupingProvider': 'graphistry',
            'analysisDiagnostics': {'analysisStatus': 'partial'},
        },
    }


@pytest.fixture
def project(tmp_path):
    """Register one snapshot without starting graph analysis or writing source files."""
    store = ProjectStore(tmp_path)
    snapshot = tmp_path / 'rev1' / 'atlas-graph.json'
    snapshot.parent.mkdir()
    graph = graph_fixture()
    snapshot.write_text(json.dumps(graph), encoding='utf-8')
    store.projects['example'] = {'id': 'example', '_graph': str(snapshot)}
    app = FastAPI()

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        """Mirror the product's HTTP status for store failures."""
        from fastapi.responses import JSONResponse
        return JSONResponse({'error': str(error), 'code': getattr(error, 'code', None)}, status_code=error.status)

    register_progressive_map_routes(app, store)
    with TestClient(app) as client:
        yield client, store, snapshot, graph
    store.close()


def test_overview_and_details_without_hydration(project, monkeypatch):
    """A persisted derivative supports overview and paginated scopes without full graph reads."""
    client, store, snapshot, graph = project
    write_progressive_map(store.data_dir, snapshot, graph)

    def fail_read(_path):
        """Fail the test if the compact path tries to hydrate a snapshot."""
        pytest.fail('Compact route hydrated the graph')

    monkeypatch.setattr('atlas.progressive_map.read_snapshot', fail_read)
    base = '/api/projects/example'
    overview = client.get(base + '/map-overview', params={'snapshotId': 'rev1'}).json()
    assert 'nodes' not in overview and overview['groupProjects'] == {'g': {'p': 1, 'q': 1}, 'h': {'p': 1}}
    assert overview['metadata']['analysisStatus'] == 'partial'
    assert overview['communityEdges'] == graph['communityEdges']
    group = client.get(base + '/map-group', params={'snapshotId': 'rev1', 'groupId': 'g', 'limit': 1}).json()
    assert [node['id'] for node in group['nodes']] == ['a']
    assert 'attributeFacts' not in group['nodes'][0]
    assert group['edges'] == [] and group['total'] == 2 and group['nextOffset'] == 1
    project_page = client.get(base + '/map-group', params={'snapshotId': 'rev1', 'projectScope': 'p'}).json()
    assert [node['id'] for node in project_page['nodes']] == ['a', 'c']
    assert project_page['edges'] == [graph['edges'][1]]
    assert project_page['nextOffset'] is None


def test_legacy_bootstrap_and_snapshot_fence(project):
    """Existing maps get a derivative lazily and changed snapshot IDs cannot reuse it."""
    client, store, snapshot, _graph = project
    base = '/api/projects/example'
    assert not list(snapshot.parent.glob('*.sqlite'))
    assert client.get(base + '/map-overview', params={'snapshotId': 'rev1'}).status_code == 200
    assert len(list(snapshot.parent.glob('*.sqlite'))) == 1
    assert client.get(base + '/map-overview', params={'snapshotId': 'other'}).status_code == 409
    assert client.get(base + '/map-overview', params={'snapshotId': 'rev1', 'algorithm': 'louvain'}).status_code == 400
    assert client.get(base + '/map-group', params={'snapshotId': 'rev1'}).status_code == 400
    assert client.get(base + '/map-group', params={'snapshotId': 'rev1', 'groupId': 'g', 'limit': 201}).status_code == 422
    assert store.projects['example']['_graph'] == str(snapshot)


def test_legacy_bootstrap_deduplicates_parallel_requests(project, monkeypatch):
    """Concurrent readers of a legacy map perform one full read and share its derivative."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from atlas import progressive_map

    client, _store, _snapshot, _graph = project
    read = progressive_map.read_snapshot
    reads = []

    def record_read(path):
        """Count real hydration calls while keeping the graph decoder in use."""
        reads.append(path)
        return read(path)

    monkeypatch.setattr(progressive_map, 'read_snapshot', record_read)
    barrier = Barrier(8)

    def request(_index):
        """Start concurrent requests with identical revision pins."""
        barrier.wait(timeout=5)
        return client.get('/api/projects/example/map-overview', params={'snapshotId': 'rev1'})

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(request, range(8)))
    assert len(reads) == 1
    assert all(response.status_code == 200 and response.content == responses[0].content for response in responses)


def test_canonical_replacement_refreshes_derivative(project):
    """Replacing a legacy document cannot keep stale grouping facts in its index."""
    client, _store, snapshot, graph = project
    url = '/api/projects/example/map-overview'
    params = {'snapshotId': 'rev1'}
    assert 'changed label' not in client.get(url, params=params).text
    graph['communities'][0]['label'] = 'changed label'
    snapshot.write_text(json.dumps(graph), encoding='utf-8')
    assert 'changed label' in client.get(url, params=params).text
    snapshot.unlink()
    assert client.get(url, params=params).status_code == 409


def test_dangling_edges_are_reported_and_do_not_block_preview(project):
    """A non-renderable edge remains counted but does not break saved overview publication."""
    client, store, snapshot, graph = project
    graph['edges'].append({'source': 'a', 'target': 'missing', 'relation': 'calls'})
    write_progressive_map(store.data_dir, snapshot, graph)
    payload = client.get('/api/projects/example/map-overview', params={'snapshotId': 'rev1'}).json()
    assert payload['metadata']['edgeCount'] == 3
    assert payload['metadata']['omittedEdges'] == 1


def test_same_length_replacement_with_preserved_mtime(project):
    """A replacement under the same revision and mtime invalidates the index."""
    import os

    client, _store, snapshot, _graph = project
    url = '/api/projects/example/map-overview'
    params = {'snapshotId': 'rev1'}
    assert client.get(url, params=params).status_code == 200
    stat = snapshot.stat()
    replacement = snapshot.with_name('replacement.json')
    replacement.write_bytes(snapshot.read_bytes().replace(b'"nodeCount": 2', b'"nodeCount": 7', 1))
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    replacement.replace(snapshot)
    assert client.get(url, params=params).json()['communities'][0]['nodeCount'] == 7


def test_oversized_page_reports_full_map_fallback(project):
    """A bounded page that exceeds the byte limit returns a controlled error."""
    client, store, snapshot, graph = project
    graph['nodes'][0]['label'] = 'a' * (1024 * 1024)
    write_progressive_map(store.data_dir, snapshot, graph)
    base = '/api/projects/example'
    assert client.get(base + '/map-overview', params={'snapshotId': 'rev1'}).status_code == 200
    response = client.get(base + '/map-group', params={'snapshotId': 'rev1', 'groupId': 'g'})
    assert response.status_code == 503 and response.json()['code'] == 'MAP_PREVIEW_UNAVAILABLE'


def test_corrupt_derivative_rebuilds_on_read(project):
    """A damaged preview is regenerated from the canonical snapshot."""
    import sqlite3

    client, _store, snapshot, graph = project
    url = '/api/projects/example/map-overview'
    params = {'snapshotId': 'rev1'}
    assert client.get(url, params=params).status_code == 200
    with sqlite3.connect(next(snapshot.parent.glob('*.sqlite'))) as db:
        db.execute("UPDATE overview SET data = '[]'")
    assert client.get(url, params=params).json()['communities'] == graph['communities']
