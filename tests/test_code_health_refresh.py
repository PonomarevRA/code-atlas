"""Health refresh is independent of map success, source files and newer revisions."""
import json
from pathlib import Path
from threading import Event
import time
import pytest
from fastapi.testclient import TestClient
from atlas import store
from atlas.code_health import enrich_code_health
from atlas.server import create_app
from test_jobs import add_project, fake_build, wait_for_job


def wait_refresh(client, project, *, terminal=True):
    """Bound asynchronous tests while retaining the exact status returned by the API."""
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        result=client.get(f'/api/projects/{project}/code-health/refresh').json()
        ready = result['status'] not in {'queued','running','not-requested'} if terminal else result['status']=='running'
        if ready:
            return result
        time.sleep(.01)
    pytest.fail(f'Health refresh did not reach expected state: {result}')


def test_auto_refresh_deduplicates_and_preserves_raw_snapshot(tmp_path,source_project,monkeypatch):
    """Map publication remains available while a separate captured-fact job is running."""
    monkeypatch.setattr(store,'build_graph',fake_build)
    app=create_app(tmp_path/'state');entered,release=Event(),Event()
    def slow(graph):
        """Hold only the health worker, after code/source extraction has already finished."""
        entered.set();assert release.wait(8)
        return enrich_code_health(graph)
    app.state.health_refresh.evaluate=slow
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        project,job=add_project(client,source_project)
        try:
            assert entered.wait(5)
            path=Path(app.state.store.projects[project]['_graph']);before=path.read_bytes()
            assert client.get(f'/api/projects/{project}/graph').status_code==200
            assert client.get('/api/projects').json()['activeJob'] is None
            active=wait_refresh(client,project,terminal=False)
            duplicate=client.post(f'/api/projects/{project}/code-health/refresh',json={'snapshotId':job['id']})
            assert duplicate.status_code==202 and duplicate.json()['id']==active['id']
            assert client.post(f'/api/projects/{project}/code-health/refresh',json={'snapshotId':job['id'],'sourceMode':'working-tree'}).status_code==422
            # The job owns captured facts, so removing the analysed file cannot change its result.
            for source in source_project.glob('*.py'):source.unlink()
        finally:release.set()
        completed=wait_refresh(client,project)
        assert completed['status']=='completed'
        graph=client.get(f'/api/projects/{project}/graph').json()
        assert graph['metadata']['healthRefresh']['jobId']==completed['id']
        assert path.read_bytes()==before
        assert client.get(f'/api/projects/{project}/code-health/report').json()['refresh']['id']==completed['id']
    with TestClient(create_app(tmp_path/'state'),base_url='http://127.0.0.1:8765') as reopened:
        assert wait_refresh(reopened,project)['status']=='completed'
        assert reopened.get(f'/api/projects/{project}/graph').json()['metadata']['healthRefresh']['jobId']==completed['id']


def test_old_refresh_cannot_publish_over_newer_map(tmp_path,source_project,monkeypatch):
    """An old job becomes superseded; a new snapshot keeps its own derived status and identity."""
    monkeypatch.setattr(store,'build_graph',fake_build)
    app=create_app(tmp_path/'state');entered,release=Event(),Event();count=0
    def first_slow(graph):
        """Only the first health job waits, allowing a second map build to publish independently."""
        nonlocal count
        count+=1
        if count==1:entered.set();assert release.wait(8)
        return enrich_code_health(graph)
    app.state.health_refresh.evaluate=first_slow
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        project,first=add_project(client,source_project)
        try:
            assert entered.wait(5)
            old_path=Path(app.state.store.projects[project]['_graph'])
            second=client.post(f'/api/projects/{project}/rebuild',json={}).json()['job']
            assert wait_for_job(client,second['id'])['status']=='completed'
            assert client.post(f'/api/projects/{project}/code-health/refresh',json={'snapshotId':first['id']}).status_code==409
        finally:release.set()
        assert wait_refresh(client,project)['snapshotId']==second['id']
        assert not old_path.with_name('code-health-overlay.json').exists()
        old=json.loads(old_path.with_name('code-health-refresh.json').read_text())
        assert old['status']=='superseded'


def test_health_failure_does_not_fail_map_and_retry_is_explicit(tmp_path,source_project,monkeypatch):
    """Scoring failure leaves the map/raw facts usable and gives a retryable health job."""
    monkeypatch.setattr(store,'build_graph',fake_build)
    app=create_app(tmp_path/'state')
    def broken(graph):
        """Simulate a failed derived calculation, not a failed source build."""
        raise ValueError('fixture health failure')
    app.state.health_refresh.evaluate=broken
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        project,job=add_project(client,source_project)
        failed=wait_refresh(client,project);assert failed['status']=='failed'
        assert client.get(f'/api/jobs/{job["id"]}').json()['status']=='completed'
        assert client.get(f'/api/projects/{project}/graph').status_code==200
        app.state.health_refresh.evaluate=enrich_code_health
        retried=client.post(f'/api/projects/{project}/code-health/refresh',json={'snapshotId':job['id']})
        assert retried.status_code==202 and retried.json()['id']!=failed['id']
        assert wait_refresh(client,project)['status']=='completed'


def test_refresh_never_turns_unknown_dependencies_into_health_score(tmp_path,source_project,monkeypatch):
    """Completed means job completion, while unresolved calls still produce an unknown H."""
    from atlas.code_health_source import parse_health_source
    def build(root,output):
        """Capture a real Python declaration with an unresolved external call."""
        graph=fake_build(root,output)
        graph['nodes']=parse_health_source('a.py',b'def run():\n    return unknown_client.send()\n')['methods']
        return graph
    monkeypatch.setattr(store,'build_graph',build)
    with TestClient(create_app(tmp_path/'state'),base_url='http://127.0.0.1:8765') as client:
        project,job=add_project(client,source_project)
        completed=wait_refresh(client,project)
        assert completed['status']=='completed' and completed['resultStatus']=='partial'
        graph=client.get(f'/api/projects/{project}/graph').json()
        assert graph['nodes'][0]['health']['score'] is None
        assert graph['nodes'][0]['health']['status']=='partial'
        path=Path(client.app.state.store.projects[project]['_graph']);overlay=path.with_name('code-health-overlay.json')
        overlay.write_text('broken derived JSON')
        assert client.get(f'/api/projects/{project}/graph').status_code==200
        assert client.get(f'/api/projects/{project}/code-health/refresh').json()['status']=='unavailable'


def test_overlay_requires_exact_snapshot_hash_and_current_analyzer(tmp_path):
    """Old or mismatched derivative contracts are ignored while captured source remains readable."""
    from atlas.code_health import ANALYZER_VERSION
    from atlas.code_health_policy import policy
    from atlas.health_snapshot_overlay import file_stamp, read_overlay, snapshot_hash
    snapshot = tmp_path / 'snapshot' / 'graph.json'
    snapshot.parent.mkdir()
    snapshot.write_text('{"nodes": []}')
    overlay = {'snapshotId': 'snapshot', 'sourceStamp': list(file_stamp(snapshot)),
               'sourceHash': snapshot_hash(snapshot, file_stamp(snapshot)),
               'policyVersion': policy()['version'], 'analyzerVersion': ANALYZER_VERSION,
               'nodes': {}, 'codeHealth': {}}
    derived = snapshot.with_name('code-health-overlay.json')
    derived.write_text(json.dumps(overlay))
    assert read_overlay(snapshot) == overlay
    for key, bad_value in [('sourceHash', 'wrong-hash'), ('analyzerVersion', 'code-health-old'),
                           ('policyVersion', 'obsolete-policy'), ('snapshotId', 'other-snapshot')]:
        derived.write_text(json.dumps({**overlay, key: bad_value}))
        assert read_overlay(snapshot) is None
    legacy = {key: value for key, value in overlay.items() if key not in {'sourceHash', 'analyzerVersion'}}
    derived.write_text(json.dumps(legacy))
    assert read_overlay(snapshot) is None


def test_stale_evaluator_cannot_publish_as_new_analyzer(tmp_path, source_project, monkeypatch):
    """The worker verifies the returned analyzer rather than relabelling stale output with its own version."""
    monkeypatch.setattr(store, 'build_graph', fake_build)
    app = create_app(tmp_path / 'state')
    def stale(graph):
        """Simulate an old evaluator completing after the active producer contract changed."""
        result = enrich_code_health(graph)
        result['metadata']['codeHealth']['analyzerVersion'] = 'obsolete-analyzer'
        return result
    app.state.health_refresh.evaluate = stale
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        project, _ = add_project(client, source_project)
        assert wait_refresh(client, project)['status'] == 'superseded'
        path = Path(app.state.store.projects[project]['_graph'])
        assert not path.with_name('code-health-overlay.json').exists()
