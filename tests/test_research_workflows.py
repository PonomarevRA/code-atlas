"""Real LangGraph/SQLite jobs preserve activation, snapshot and local source boundaries."""
import json
import socket
import threading
import time
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from atlas.mod_store import ModStore, ModError
from atlas.research.api import register_research_routes
from atlas.research.evidence import EvidenceCollector
from atlas.research.models import StartResearch, ResearchError
from atlas.research.service import ResearchService
from atlas import store
from test_grouping_cache import legacy_store


class LocalReferences:
    """An imported reference fixture proves edition capture without a network or embedding model."""
    def __init__(self):
        self.store = self

    def search(self, query, **kwargs):
        assert kwargs['mode'] == 'fulltext'
        return {'results': [{'packId': 'csharp-v12', 'documentId': query, 'title': 'C# ' + query,
                             'version': '12', 'locale': 'ru', 'edition': 'test', 'sourceUrl': 'https://example.invalid/docs'}]}

    def document(self, pack, document, **kwargs):
        return {'text': f'Правила языка для {document}.', 'nextOffset': None}


class LocalLibraries:
    def search(self, *args, **kwargs):
        assert kwargs['mode'] == 'fulltext'
        return {'results': []}


@pytest.fixture
def research(tmp_path):
    projects, pid, graph_path, graph = legacy_store(tmp_path)
    source = tmp_path / 'Calc.cs'
    source.write_text('''public class Calc {
/// <summary>Считает значение с ограничением отрицательного входа.</summary>
public int Total(int value) {
  if (value < 0) return 0;
  return Add(value, 2);
}
/// <summary>Складывает два числа.</summary>
private int Add(int x, int y) => x + y;
}
''')
    graph.update(nodes=[{'id': 'total', 'label': 'Total()', 'file': 'Calc.cs', 'line': 3, 'kind': 'method'},
                        {'id': 'add', 'label': 'Add()', 'file': 'Calc.cs', 'line': 8, 'kind': 'method'}],
                 edges=[{'source': 'total', 'target': 'add', 'relation': 'calls'}])
    graph['metadata']['snapshotId'] = 'first'
    store.write_json(graph_path, graph)
    projects.mods = ModStore(projects.data_dir, tmp_path / 'no-bundled')
    for identifier, kind, requires in [('test-guide', 'research', []), ('test-docs', 'documentation', ['test-guide'])]:
        folder = projects.mods.directory / identifier
        folder.mkdir(parents=True)
        (folder / 'mod.json').write_text(json.dumps({'apiVersion': 1, 'id': identifier, 'name': identifier, 'version': '1',
                                                    'requires': requires, 'workflows': [{'id': 'run', 'label': 'Local facts', 'kind': kind}]}))
        item = next(item for item in projects.mods.catalog()['mods'] if item['id'] == identifier)
        projects.mods.enable(identifier, item['fingerprint'], True)
    service = ResearchService(projects, LocalReferences(), LocalLibraries())
    yield service, projects, pid, source, graph_path
    service.close()
    projects.close()


def request(service, pid, mod='test-docs', **changes):
    fingerprint = next(item['fingerprint'] for item in service.projects.mods.catalog()['mods'] if item['id'] == mod)
    return StartResearch(projectId=pid, snapshotId='first', modId=mod, fingerprint=fingerprint, workflowId='run',
                         nodeId='total', question='Что делает Total?', requestKey=uuid4().hex, **changes)


def finished(service, identifier):
    """Bound the wait for actual asynchronous work; surface persisted errors on failure."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        run = service.records.get(identifier)
        if run['status'] not in {'queued', 'running'}:
            return run
        time.sleep(.02)
    pytest.fail('Research did not finish within 15 seconds')


def test_real_graph_local_document_no_network_and_immutable_source(research, monkeypatch):
    service, projects, pid, source, graph_path = research
    before = (source.read_bytes(), graph_path.read_bytes())
    connections = []
    def forbidden(*args, **kwargs):
        connections.append(args)
        raise AssertionError('Local facts workflow attempted network')
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    run = service.start(request(service, pid))
    done = finished(service, run['id'])
    assert done['status'] == 'completed', done
    assert not connections
    document = done['document']
    assert document['generationMode'] == 'local-facts' and document['semanticReview'] == 'not-performed'
    assert 'Считает значение' in document['markdown'] and 'Складывает два числа' in document['markdown']
    assert 'value < 0' in document['markdown'] and 'выполненных вызовов' not in document['markdown']
    evidence = service.records.evidence(run['id'])
    assert len([item for item in evidence if item['kind'] == 'code']) == 2
    assert any(item['kind'] == 'language' and item['version'] == '12' for item in evidence)
    assert all(item['id'] in document['markdown'] for item in evidence)
    assert document['freshness']['status'] == 'verified-at-capture'
    assert before == (source.read_bytes(), graph_path.read_bytes())
    assert (service.records.root / 'checkpoints.sqlite').is_file()
    source.write_text(source.read_text() + '// changed\n')
    assert service.freshness(run['id'])['status'] == 'stale'
    assert service.records.get(run['id'])['document'] == document


def test_request_retry_limits_and_mod_dependency(research):
    service, projects, pid, _, _ = research
    body = request(service, pid, maxNodes=1, maxDepth=0)
    run = service.start(body)
    assert service.start(body)['id'] == run['id']
    done = finished(service, run['id'])
    assert done['status'] == 'completed', done
    assert len([e for e in service.records.evidence(run['id']) if e['kind'] == 'code']) == 1
    assert any('Глубина' in warning for warning in done['document']['warnings'])
    with pytest.raises(ResearchError):
        service.start(body.model_copy(update={'question': 'another'}))
    projects.mods.enable('test-guide', None, False)
    with pytest.raises(ModError):
        service.start(request(service, pid))
    assert done['document'] == service.records.get(run['id'])['document']


def test_checkpoint_recovery_is_explicit_and_capture_replay_idempotent(research, monkeypatch):
    service, projects, pid, _, _ = research
    original = EvidenceCollector.capture_node
    failures = [True]
    def fail_after_capture(self, identifier):
        result = original(self, identifier)
        if failures:
            failures.pop()
            raise RuntimeError('Simulated crash after evidence, before checkpoint')
        return result
    monkeypatch.setattr(EvidenceCollector, 'capture_node', fail_after_capture)
    run = service.start(request(service, pid))
    assert finished(service, run['id'])['status'] == 'failed'
    captured = service.records.evidence(run['id'])
    assert len(captured) == 1
    service.close()
    restored = ResearchService(projects, LocalReferences(), LocalLibraries())
    try:
        assert not restored.pending
        restored.resume(run['id'])
        done = finished(restored, run['id'])
        assert done['status'] == 'completed', done
        assert sum(item['id'] == captured[0]['id'] for item in restored.records.evidence(run['id'])) == 1
        with pytest.raises(ResearchError):
            restored.resume(run['id'])
    finally:
        restored.close()


def test_cancel_during_capture_prevents_publication(research, monkeypatch):
    service, _, pid, _, _ = research
    original = EvidenceCollector.capture_node
    entered, release = threading.Event(), threading.Event()
    def paused(self, identifier):
        entered.set()
        assert release.wait(5)
        return original(self, identifier)
    monkeypatch.setattr(EvidenceCollector, 'capture_node', paused)
    run = service.start(request(service, pid))
    assert entered.wait(5)
    try:
        assert service.cancel(run['id'])['status'] == 'cancelled'
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while run['id'] in service.pending and time.monotonic() < deadline:
        time.sleep(.02)
    assert service.records.get(run['id'])['document'] is None
    assert service.records.get(run['id'])['status'] == 'cancelled'


@pytest.mark.parametrize('moment', ['before-first-check', 'during-capture'])
def test_cooperative_shutdown_preserves_explicit_recovery(research, monkeypatch, moment):
    """A stop before or during a checkpoint remains interrupted and resumes explicitly after restart."""
    service, projects, pid, _, _ = research
    if moment == 'before-first-check':
        with monkeypatch.context() as patch:
            patch.setattr(service, '_enqueue', lambda identifier: service.pending.add(identifier))
            run = service.start(request(service, pid))
            service.request_stop()
            service._run(run['id'])
    else:
        original = EvidenceCollector.capture_node
        entered, release = threading.Event(), threading.Event()

        def paused(collector, identifier):
            """Hold one real captured-fact step until the application has requested shutdown."""
            entered.set()
            assert release.wait(5)
            return original(collector, identifier)

        with monkeypatch.context() as patch:
            patch.setattr(EvidenceCollector, 'capture_node', paused)
            run = service.start(request(service, pid))
            try:
                assert entered.wait(5)
                service.request_stop()
            finally:
                release.set()
            assert finished(service, run['id'])['status'] == 'interrupted'
            service.close()

    interrupted = service.records.get(run['id'])
    assert interrupted['status'] == 'interrupted' and interrupted['document'] is None
    assert interrupted['error'] == ''
    service.close()
    restored = ResearchService(projects, LocalReferences(), LocalLibraries())
    try:
        assert not restored.pending
        assert restored.records.get(run['id'])['status'] == 'interrupted'
        restored.resume(run['id'])
        assert finished(restored, run['id'])['status'] == 'completed'
    finally:
        restored.close()


@pytest.mark.parametrize('terminal', ['cancelled', 'completed'])
def test_shutdown_does_not_replace_terminal_research_result(research, monkeypatch, terminal):
    """A stop racing an active worker preserves a user cancellation or an immutable published result."""
    service, _, pid, _, _ = research
    original = EvidenceCollector.capture_node
    entered, release = threading.Event(), threading.Event()

    def paused(collector, identifier):
        """Pause a real worker so the terminal status wins before its next cancellation checkpoint."""
        entered.set()
        assert release.wait(5)
        return original(collector, identifier)

    monkeypatch.setattr(EvidenceCollector, 'capture_node', paused)
    run = service.start(request(service, pid))
    document = None
    try:
        assert entered.wait(5)
        if terminal == 'cancelled':
            assert service.cancel(run['id'])['status'] == terminal
        else:
            document = {'markdown': 'Already published immutable result'}
            assert service.records.transition(run['id'], 'completed', 'Готово', expected={'running'}, document=document)
        service.request_stop()
    finally:
        release.set()
    service.close()
    final = service.records.get(run['id'])
    assert final['status'] == terminal and final['document'] == document


def test_source_change_blocks_publication(research, monkeypatch):
    service, _, pid, source, _ = research
    original = EvidenceCollector.capture_references
    def mutate(self):
        source.write_text(source.read_text() + '// stale before publishing\n')
        return original(self)
    monkeypatch.setattr(EvidenceCollector, 'capture_references', mutate)
    run = service.start(request(service, pid))
    done = finished(service, run['id'])
    assert done['status'] == 'stale' and done['document'] is None


def test_http_scoped_history_validation_and_markdown(research):
    service, _, pid, _, _ = research
    app = FastAPI()
    register_research_routes(app, service)
    client = TestClient(app)
    body = request(service, pid)
    run = client.post('/api/research/runs', json=body.model_dump()).json()
    done = finished(service, run['id'])
    assert done['status'] == 'completed', done
    response = client.get(f"/api/research/runs/{run['id']}/document.md")
    assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
    assert client.get('/api/research/runs?projectId=another').json()['runs'] == []
    assert client.get('/api/research/runs/../../etc').status_code == 404
    assert client.post('/api/research/runs', json={**body.model_dump(), 'maxNodes': 13}).status_code == 422
    assert client.post('/api/research/runs', json={**body.model_dump(), 'model': 'not-allowed'}).status_code == 422
    assert client.get('/api/research/capabilities').json()['modelConnected'] is False


@pytest.mark.parametrize('change', ['snapshot', 'mod'])
def test_changed_revision_blocks_publication(research, monkeypatch, change):
    """Checkpoints cannot publish after a snapshot replacement or active package edit."""
    service, projects, pid, _, graph_path = research
    original = EvidenceCollector.capture_references
    def mutate(self):
        result = original(self)
        if change == 'snapshot':
            graph = json.loads(graph_path.read_text())
            graph['metadata']['snapshotId'] = 'second'
            replacement = graph_path.parent.parent / 'second' / 'atlas-graph.json'
            store.write_json(replacement, graph)
            projects.projects[pid]['_graph'] = str(replacement)
        else:
            (projects.mods.directory / 'test-docs' / 'README.md').write_text('Changed contents')
        return result
    monkeypatch.setattr(EvidenceCollector, 'capture_references', mutate)
    run = service.start(request(service, pid))
    done = finished(service, run['id'])
    assert done['status'] in {'stale', 'failed'} and done['document'] is None


def test_markdown_prose_is_escaped_but_source_is_literal():
    """Exported prose cannot introduce images/HTML; a source fence outgrows embedded backticks."""
    from atlas.research.documents import compose_document
    run = {'workflowKind':'research', 'question':'![track](https://example.invalid)', 'snapshotId':'first', 'projectId':'p'}
    evidence = [{'id':'E123', 'kind':'code', 'nodeId':'x', 'label':'<script>bad</script>', 'file':'x.cs',
                 'line':1, 'relations':[], 'source':'```\n<script>literal</script>', 'description':{}}]
    text = compose_document(run, evidence, [])['markdown']
    assert r'\!\[track\]' in text and r'\<script\>' in text
    assert '````\n```\n<script>literal</script>\n````' in text
