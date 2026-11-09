"""Adversarial local-fact jobs exercise the installed LangGraph and durable limits."""
import json
from hashlib import sha256
import threading
import time

import pytest

from atlas.reference_documents import ReferenceError
from atlas.research import evidence as evidence_module
from atlas.research.evidence import EvidenceCollector
from atlas.research.models import ResearchError
from atlas.research.service import ResearchContext
from atlas.research.graphs import build_workflow
from atlas import store
from test_research_workflows import finished, request, research


def test_unavailable_source_is_partial_not_verified(research):
    """A map can still supply facts when its file is gone, but cannot verify that file."""
    service, _, pid, source, _ = research
    source.unlink()
    run = service.start(request(service, pid))
    done = finished(service, run['id'])
    assert done['status'] == 'completed', done
    freshness = done['document']['freshness']
    assert freshness['status'] == 'partial'
    assert freshness['unavailableFiles'] == ['Calc.cs']
    assert freshness['changedFiles'] == []
    assert any('актуальность этих файлов не проверена' in item for item in done['document']['warnings'])
    assert all(item['sourceStatus'] == 'unavailable' for item in service.records.evidence(run['id']))


def test_changed_hash_takes_precedence_over_unavailable_source(research):
    """Partial evidence cannot hide a known mismatch and permit publication."""
    service, projects, pid, source, _ = research
    run, _ = service.records.create(request(service, pid).model_dump())
    service.records.capture(run['id'], {'kind': 'code', 'file': 'Calc.cs',
                                       'sourceHash': sha256(source.read_bytes()).hexdigest()})
    service.records.capture(run['id'], {'kind': 'code', 'file': 'Missing.cs'})
    source.write_text(source.read_text() + '// changed\n')
    collector = EvidenceCollector(projects, service.records, service.references, service.libraries, run)
    result = collector.verify()
    assert result['status'] == 'stale'
    assert result['changedFiles'] == ['Calc.cs']
    assert result['unavailableFiles'] == ['Missing.cs']


def test_capture_does_not_highlight_and_still_rejects_read_race(research, monkeypatch):
    """Raw-byte verification preserves the race guard without a second source presentation."""
    service, _, pid, source, _ = research
    original = evidence_module.build_node_description

    def mutate_after_description(*args):
        result = original(*args)
        source.write_text(source.read_text() + '// changed during capture\n')
        return result

    original_read = evidence_module.read_node_source
    presentation_options = []

    def read_facts(*args, **kwargs):
        presentation_options.append(kwargs.get('include_syntax'))
        return original_read(*args, **kwargs)

    monkeypatch.setattr(evidence_module, 'build_node_description', mutate_after_description)
    monkeypatch.setattr(evidence_module, 'read_node_source', read_facts)
    run = service.start(request(service, pid))
    done = finished(service, run['id'])
    assert done['status'] == 'stale', done
    assert 'во время чтения' in done['error']
    assert service.records.evidence(run['id']) == []
    assert presentation_options == [False]


def test_history_does_not_decode_document_bodies(research):
    """History uses metadata only, even when a saved document is not deserializable."""
    service, _, pid, _, _ = research
    run = service.start(request(service, pid))
    assert finished(service, run['id'])['status'] == 'completed'
    with service.records.connection() as db:
        db.execute('UPDATE runs SET document=? WHERE id=?', ('not-json', run['id']))
    history = service.records.history(pid)
    assert len(history) == 1 and history[0]['id'] == run['id']
    assert 'document' not in history[0]


def test_real_python_flow_and_bounded_checkpoint_state(research, monkeypatch):
    """The shared workflow captures Python flow while checkpoints retain only IDs/cursors."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    service, _, pid, source, graph_path = research
    python_source = source.with_name('calc.py')
    python_source.write_text('''def total(value):
    """Clamp negative input and add two to nonnegative values."""
    if value < 0:
        return 0
    return add(value, 2)

def add(x, y):
    """Return the sum of the two inputs."""
    return x + y
''')
    graph = json.loads(graph_path.read_text())
    for node, line in zip(graph['nodes'], (1, 7)):
        node.update(file='calc.py', line=line, language='python', label=node['id'] + '()')
    store.write_json(graph_path, graph)
    original = EvidenceCollector.capture_references

    def stop_before_references(self):
        raise RuntimeError('Persist bounded checkpoint for inspection')

    monkeypatch.setattr(EvidenceCollector, 'capture_references', stop_before_references)
    run = service.start(request(service, pid))
    assert finished(service, run['id'])['status'] == 'failed'
    with SqliteSaver.from_conn_string(str(service.records.root / 'checkpoints.sqlite')) as saver:
        workflow = build_workflow(ResearchContext(service, service.records.get(run['id'])), saver)
        checkpoint = workflow.get_state({'configurable': {'thread_id': run['id']}})
    assert set(checkpoint.values) == {'selected', 'cursor', 'warnings'}
    assert checkpoint.values['cursor'] == 2
    assert len(checkpoint.values['selected']) == 2
    assert all(set(item) == {'id', 'depth'} for item in checkpoint.values['selected'])
    evidence = service.records.evidence(run['id'])
    assert all(item['description']['methodFlow']['steps'] for item in evidence)
    assert 'value < 0' in evidence[0]['source']
    deadline = time.monotonic() + 5
    while run['id'] in service.pending and time.monotonic() < deadline:
        time.sleep(.01)
    monkeypatch.setattr(EvidenceCollector, 'capture_references', original)
    service.resume(run['id'])
    done = finished(service, run['id'])
    assert done['status'] == 'completed', done
    assert 'Clamp negative input' in done['document']['markdown']


def test_queue_bound_retry_and_queued_cancellation(research, monkeypatch):
    """Four admitted runs include the active job; retries cannot consume a fifth slot."""
    service, _, pid, _, _ = research
    original = EvidenceCollector.capture_node
    entered, release = threading.Event(), threading.Event()

    def paused(self, identifier):
        entered.set()
        assert release.wait(5)
        return original(self, identifier)

    monkeypatch.setattr(EvidenceCollector, 'capture_node', paused)
    first = service.start(request(service, pid))
    assert entered.wait(5)
    try:
        queued = [request(service, pid) for _ in range(3)]
        runs = [service.start(body) for body in queued]
        with pytest.raises(ResearchError, match='Очередь'):
            service.start(request(service, pid))
        assert service.start(queued[-1])['id'] == runs[-1]['id']
        assert service.cancel(runs[0]['id'])['status'] == 'cancelled'
        assert service.cancel(runs[0]['id'])['status'] == 'cancelled'
    finally:
        release.set()
    assert finished(service, first['id'])['status'] == 'completed'
    assert service.records.get(runs[0]['id'])['document'] is None
    assert service.records.evidence(runs[0]['id']) == []
    assert all(finished(service, item['id'])['status'] == 'completed' for item in runs[1:])


def test_persistent_evidence_budgets_and_idempotency(research):
    """Limits count UTF-8 bytes and distinct evidence; replay at the limit is permitted."""
    service, _, pid, _, _ = research

    def empty_run():
        return service.records.create(request(service, pid).model_dump())[0]['id']

    identifier = empty_run()
    for index in range(24):
        service.records.capture(identifier, {'kind': 'code', 'source': str(index)})
    assert service.records.capture(identifier, {'kind': 'code', 'source': '23'})
    with pytest.raises(ResearchError, match='лимит сохранённых'):
        service.records.capture(identifier, {'kind': 'code', 'source': '25'})
    with pytest.raises(ResearchError, match='одного источника'):
        service.records.capture(empty_run(), {'source': 'я' * 24_000})
    identifier = empty_run()
    for index in range(11):
        service.records.capture(identifier, {'index': index, 'source': 'x' * 45_000})
    with pytest.raises(ResearchError, match='лимит сохранённых'):
        service.records.capture(identifier, {'source': 'x' * 45_000})


@pytest.mark.parametrize('expected_failure', [True, False])
def test_reference_failure_preserves_safe_evidence_and_explicit_recovery(research, monkeypatch, expected_failure):
    """Unavailable imported docs degrade locally; unexpected errors require an explicit resume."""
    service, _, pid, _, _ = research
    original = service.references.search

    def unavailable(*args, **kwargs):
        if expected_failure:
            raise ReferenceError('Imported pack is unavailable')
        raise RuntimeError('internal-test-detail-must-not-leak')

    monkeypatch.setattr(service.references, 'search', unavailable)
    run = service.start(request(service, pid))
    done = finished(service, run['id'])
    assert len(service.records.evidence(run['id'])) == 2
    if expected_failure:
        assert done['status'] == 'completed', done
        assert any('Часть локальных справочников' in item for item in done['document']['warnings'])
    else:
        assert done['status'] == 'failed' and done['document'] is None
        assert 'internal-test-detail' not in done['error']
        # The final SQLite transition precedes worker cleanup by a small interval.
        deadline = time.monotonic() + 5
        while run['id'] in service.pending and time.monotonic() < deadline:
            time.sleep(.01)
        monkeypatch.setattr(service.references, 'search', original)
        service.resume(run['id'])
        assert finished(service, run['id'])['status'] == 'completed'
