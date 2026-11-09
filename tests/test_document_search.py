"""Captured-only search lifecycle и независимость от исходного code corpus."""
import pytest
import json
from concurrent.futures import ThreadPoolExecutor

from atlas.document_search import DocumentSearch
from atlas.documents.corpus_adapter import capture_document_corpus
from atlas.documents.models import DocumentError
from atlas.documents.store import DocumentStore
from atlas.semantic_index import IndexDatabase
from test_document_corpus_adapter import captured, _Embedding
from atlas.process_lock import ProcessLock


class LocalFixtureEmbedding(_Embedding):
    """Тестовый local backend проверяет индексный контракт, не качество модели."""

    def available(self):
        """No weight download or ONNX runtime is required for deterministic fixture vectors."""
        return True

    def metadata(self):
        """Expose the same cheap status contract as the real local embedding provider."""
        return {**self.identity(), 'available': True, 'offline': True}


def test_parallel_search_pins_are_preserved_and_external_busy_is_retryable(tmp_path):
    """Параллельные карты не теряют pins; межпроцессный конфликт возвращает 409."""
    service = DocumentSearch(tmp_path, LocalFixtureEmbedding())
    scopes = [{'documentMapId': 'map-' + str(i), 'documentSnapshotId': 'snapshot-' + str(i)} for i in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(service._pin_manifest, scopes))
    path = tmp_path / 'document-search' / 'references.json'
    assert len(json.loads(path.read_text())['references']) == len(scopes)
    with ProcessLock(path.with_name('reference-write.lock')):
        with pytest.raises(DocumentError) as failure:
            service._pin_manifest(scopes[0])
    assert failure.value.status == 409 and failure.value.code == 'SEARCH_REFERENCES_BUSY'
    assert len(json.loads(path.read_text())['references']) == len(scopes)
    service.close()


def test_document_search_build_reuse_and_historical_pairs_do_not_overwrite_code_corpus(captured, tmp_path, monkeypatch):
    """Две published document ревизии доступны отдельно после удаления originals."""
    from markdown_it import MarkdownIt
    root, old_manifest, graph, project, old_text = captured
    data_dir = tmp_path / 'search-state'
    embedding = LocalFixtureEmbedding()
    code_index = IndexDatabase(data_dir, embedding)
    old_corpus = capture_document_corpus(root, old_manifest, graph, 'code-snapshot')
    original_index = code_index.build('project', 'code-snapshot', old_corpus, lambda: None)
    service = DocumentSearch(data_dir, embedding)
    try:
        first = service.build(root, old_manifest, graph, 'project', 'code-snapshot')
        reused = service.build(root, old_manifest, graph, 'project', 'code-snapshot')
        assert first == reused
        assert first['index']['table'] != original_index['table']
        assert code_index.status('project', 'code-snapshot') == original_index
        (project / 'docs' / 'rules.md').write_text('# Updated\n\nNew captured rules.', encoding='utf-8')
        store = DocumentStore(root.parent.parent, project, 'project')
        try:
            job = store.ensure('documentation', ['docs/rules.md', 'docs/other.md'])
            result = store.wait(job['id'], 20)
            assert result['status'] == 'completed', result
            new_manifest = store.snapshot('documentation')
        finally:
            store.close()
        assert old_manifest['snapshotId'] != new_manifest['snapshotId']
        second = service.build(root, new_manifest, graph, 'project', 'code-snapshot')
        assert first['index']['table'] != second['index']['table']
        for path in (project / 'docs').iterdir():
            path.unlink()
        monkeypatch.setattr(MarkdownIt, 'parse', lambda *args, **kwargs: pytest.fail('parser during captured search'))
        old = service.search('project', 'code-snapshot', 'documentation', old_manifest['snapshotId'], 'currency')
        new = service.search('project', 'code-snapshot', 'documentation', new_manifest['snapshotId'], 'updated')
        old_rule = next(hit for hit in old['hits'] if hit['file'] == 'docs/rules.md')
        new_rule = next(hit for hit in new['hits'] if hit['file'] == 'docs/rules.md')
        assert old_rule['text'] == old_text
        assert 'New captured rules.' in new_rule['text']
        assert old['documentSnapshotId'] == old_manifest['snapshotId']
        assert new['documentSnapshotId'] == new_manifest['snapshotId']
        assert code_index.status('project', 'code-snapshot') == original_index
    finally:
        service.close()
        code_index.close()


def test_document_search_scope_never_falls_back_to_neighbor_revision(captured, tmp_path):
    """Неизвестный code/document snapshot либо проект не возвращает чужой индекс."""
    root, manifest, graph, project, _ = captured
    service = DocumentSearch(tmp_path / 'search-state', LocalFixtureEmbedding())
    try:
        service.build(root, manifest, graph, 'project', 'code-snapshot')
        for project_id, code_id, map_id, snapshot_id in [
            ('other', 'code-snapshot', 'documentation', manifest['snapshotId']),
            ('project', 'other-code', 'documentation', manifest['snapshotId']),
            ('project', 'code-snapshot', 'other-map', manifest['snapshotId']),
            ('project', 'code-snapshot', 'documentation', '0' * 64),
        ]:
            assert service.status(project_id, code_id, map_id, snapshot_id)['index'] is None
            with pytest.raises(DocumentError) as error:
                service.search(project_id, code_id, map_id, snapshot_id, 'currency')
            assert error.value.status == 409
        with pytest.raises(DocumentError) as error:
            service.build(root, manifest, graph, 'other', 'code-snapshot')
        assert error.value.code == 'PROJECT_MISMATCH'
    finally:
        service.close()


def test_missing_actual_local_model_is_truthful_and_never_reads_corpus(captured, tmp_path, monkeypatch):
    """Реальный LocalEmbedding сообщает unavailable, не пробуя сети, parsers или build."""
    import atlas.document_search as module
    monkeypatch.delenv('ATLAS_EMBEDDING_CACHE', raising=False)
    root, manifest, graph, project, _ = captured
    service = DocumentSearch(tmp_path / 'fresh-no-model')
    monkeypatch.setattr(module, 'capture_document_corpus', lambda *args, **kwargs: pytest.fail('capture before model availability'))
    try:
        state = service.status('project', 'code-snapshot', 'documentation', manifest['snapshotId'])
        assert state['status'] == 'model-unavailable' and state['modelAvailable'] is False
        with pytest.raises(DocumentError) as error:
            service.build(root, manifest, graph, 'project', 'code-snapshot')
        assert error.value.code == 'MODEL_UNAVAILABLE' and error.value.status == 503
        with pytest.raises(DocumentError) as error:
            service.search('project', 'code-snapshot', 'documentation', manifest['snapshotId'], 'currency')
        assert error.value.code == 'MODEL_UNAVAILABLE'
        assert not (tmp_path / 'fresh-no-model' / 'document-search').exists()
    finally:
        service.close()
