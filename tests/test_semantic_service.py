"""Контракт HTTP/worker: нет сети, устаревших ответов и выдуманных связей с кодом."""

from threading import Event, RLock
import time

from fastapi.testclient import TestClient
import pytest

from atlas import server, store
from atlas.semantic_service import SemanticService
from test_grouping_cache import legacy_store


class FakeEmbedding:
    """Модель без весов для проверки жизненного цикла, а не качества ранжирования."""
    def available(self):
        return True

    def metadata(self):
        return {"name": "fixture", "dimensions": 3}


class FakeDatabase:
    """Детерминированный backend для гонок публикации снимка."""
    def __init__(self):
        self.index = None
        self.calls = 0
        self.started = Event()
        self.release = Event()

    def status(self, project_id, snapshot_id):
        return self.index

    def build(self, project_id, snapshot_id, corpus, assert_current, force=False):
        self.calls += 1
        self.started.set()
        assert self.release.wait(5)
        assert_current()
        self.index = {"chunkCount": len(corpus["chunks"]), "snapshotId": snapshot_id}
        return self.index

    def search(self, project_id, snapshot_id, query, limit=8, kind="all"):
        return {"index": self.index, "hits": [{"text": "Comment", "nodeIds": ["first", "fake-call"], "distance": .15}]}

    def close(self):
        pass


def wait_job(service, job_id):
    """Дожидается наблюдаемого результата worker с коротким пределом ожидания."""
    for _ in range(200):
        job = service.get_job(job_id)
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(.01)
    pytest.fail("Documentation worker did not finish")


def test_worker_reuses_active_job_and_rejects_snapshot_race(tmp_path):
    """Смена карты перед публикацией не оставляет готовый индекс другой ревизии."""
    state, project, graph_path, graph = legacy_store(tmp_path)
    database = FakeDatabase()
    service = SemanticService(state, database, FakeEmbedding())
    try:
        job = service.submit(project, "first")["job"]
        assert database.started.wait(5)
        assert service.submit(project, "first")["job"]["id"] == job["id"]
        assert database.calls == 1
        replacement = graph_path.parent.parent / "second" / graph_path.name
        store.write_json(replacement, graph)
        state.projects[project]["_graph"] = str(replacement)
        database.release.set()
        assert wait_job(service, job["id"])["status"] == "failed"
        assert database.index is None
        assert state.get_graph(project)["metadata"]["snapshotId"] == "second"
    finally:
        database.release.set()
        state.close()
        service.close()


def test_search_keeps_existing_code_evidence_and_rechecks_snapshot(tmp_path):
    """Недействительные raw-ID отбрасываются; публикация новой карты отменяет ответ."""
    state, project, graph_path, graph = legacy_store(tmp_path)
    database = FakeDatabase()
    service = SemanticService(state, database, FakeEmbedding())
    try:
        result = service.search(project, "first", " comment ", 8, "all")
        assert result["query"] == "comment"
        assert result["hits"][0]["nodeIds"] == ["first"]
        assert [node["id"] for node in result["codeNodes"]] == ["first"]
        original = database.search
        def changed(*args, **kwargs):
            """Публикует другой снимок в середине векторного поиска."""
            value = original(*args, **kwargs)
            replacement = graph_path.parent.parent / "second" / graph_path.name
            store.write_json(replacement, graph)
            state.projects[project]["_graph"] = str(replacement)
            return value
        database.search = changed
        with pytest.raises(store.StoreError) as denied:
            service.search(project, "first", "comment", 8, "all")
        assert denied.value.status == 409
    finally:
        state.close()
        service.close()


def test_http_missing_model_is_explicit_and_does_not_disable_graph(tmp_path, monkeypatch):
    """Чистая установка работает без модели, а invalid input проверяется до поиска."""
    state, project, _, _ = legacy_store(tmp_path)
    state.close()
    monkeypatch.setenv("ATLAS_EMBEDDING_CACHE", str(tmp_path / "missing-model"))
    with TestClient(server.create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        route = f"/api/projects/{project}"
        assert client.get(route + "/graph").status_code == 200
        status = client.get(route + "/documentation-index?snapshotId=first").json()
        assert status["status"] == "model-unavailable"
        assert client.post(route + "/documentation-index", json={"snapshotId": "first"}).status_code == 503
        assert client.post(route + "/semantic-search", json={"snapshotId": "old", "query": "comment"}).status_code == 409
        assert client.post(route + "/semantic-search", json={"snapshotId": "first", "query": " "}).status_code == 400
        for extra in [{"limit": 21}, {"query": "x" * 1001}, {"kind": "source"}, {"source": "raw"}]:
            body = {"snapshotId": "first", "query": "comment", **extra}
            assert client.post(route + "/semantic-search", json=body).status_code == 422


def test_published_snapshots_defer_latest_revision_and_start_after_busy_writer(tmp_path):
    """Publication during another build is visible as deferred and never loses the latest project revision."""
    class PublicationStore:
        data_dir = tmp_path
        revisions = {'a': 'a-snapshot', 'b': 'b-first'}
        def comparison_snapshot(self, project):
            return tmp_path, {'nodes': [], 'edges': [], 'metadata': {'snapshotId': self.revisions[project]}}
        def assert_snapshot(self, project, snapshot):
            if self.revisions.get(project) != snapshot:
                raise store.StoreError('stale', 409)
    class RecordingDatabase(FakeDatabase):
        def __init__(self):
            super().__init__()
            self.snapshots = []
        def build(self, project, snapshot, corpus, assert_current, force=False):
            self.snapshots.append((project, snapshot))
            return super().build(project, snapshot, corpus, assert_current, force)
    state = PublicationStore()
    database = RecordingDatabase()
    service = SemanticService(state, database, FakeEmbedding())
    try:
        first = service.submit('a', 'a-snapshot')['job']
        assert database.started.wait(2)
        service.snapshot_published('b', 'b-first')
        assert service.status('b', 'b-first')['status'] == 'deferred'
        state.revisions['b'] = 'b-current'
        service.snapshot_published('b', 'b-current')
        assert service.status('b', 'b-current')['deferred']
        database.release.set()
        assert wait_job(service, first['id'])['status'] == 'completed'
        for _ in range(200):
            if len(database.snapshots) == 2:
                break
            time.sleep(.01)
        assert database.snapshots == [('a', 'a-snapshot'), ('b', 'b-current')]
    finally:
        database.release.set()
        service.close()


@pytest.mark.parametrize('newer_event', [False, True])
def test_deferred_item_survives_manual_writer_winning_submit_race(newer_event):
    """Deterministic interleaving preserves popped revision or a newer queued publication."""
    service = SemanticService.__new__(SemanticService)
    service.lock = RLock()
    service.closed = False
    service.active_job = None
    service.deferred = {'project': 'pending'}
    calls = []

    def competing_submit(project, snapshot):
        calls.append((project, snapshot))
        service.active_job = 'manual-winner'
        if newer_event:
            service.deferred[project] = 'newer-publication'
        raise store.StoreError('writer occupied', 409)

    service.submit = competing_submit
    service._run_deferred()
    assert calls == [('project', 'pending')]
    assert service.deferred == {'project': 'newer-publication' if newer_event else 'pending'}


def test_stale_publication_does_not_overwrite_latest_deferred_revision():
    """An event rejected by snapshot validation cannot regress the pending project revision."""
    service = SemanticService.__new__(SemanticService)
    service.lock = RLock()
    service.closed = False
    service.active_job = 'writer'
    service.deferred = {'project': 'latest'}
    service.embedder = FakeEmbedding()

    class CurrentStore:
        def assert_snapshot(self, project, snapshot):
            if snapshot != 'latest':
                raise store.StoreError('stale snapshot', 409)

    def busy_submit(project, snapshot):
        raise store.StoreError('busy or stale', 409)

    service.store = CurrentStore()
    service.submit = busy_submit
    service.snapshot_published('project', 'old')
    assert service.deferred == {'project': 'latest'}


def test_corrupt_index_http_errors_do_not_disable_graph_or_load_model(tmp_path, monkeypatch):
    """Status, wiki and semantic search share a controlled damaged-index error; normal graph stays usable."""
    state, project, _, _ = legacy_store(tmp_path)
    state.close()
    app = server.create_app(tmp_path / 'state')
    path = app.state.semantic.database._manifest_path(project, 'first')
    path.parent.mkdir(parents=True)
    path.write_text('[]')

    def forbidden(*args):
        """Repair reporting cannot trigger cold model or LanceDB initialization."""
        raise AssertionError('corrupted manifest loaded dependencies')

    monkeypatch.setattr(app.state.semantic.database, '_db', forbidden)
    monkeypatch.setattr(app.state.semantic.embedder, 'identity', forbidden)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        route = f'/api/projects/{project}'
        assert client.get(route + '/graph').status_code == 200
        replies = [client.get(route + '/documentation-index?snapshotId=first'),
                   client.post(route + '/semantic-search', json={'snapshotId': 'first', 'query': 'currency'}),
                   client.post(route + '/node-wiki', json={'snapshotId': 'first', 'nodeId': 'first'})]
        for response in replies:
            assert response.status_code == 503, response.text
            assert 'Пересоберите' in response.json()['error']
        assert client.get(route + '/graph').status_code == 200
