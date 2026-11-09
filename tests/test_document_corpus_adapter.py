"""Parser-free captured Markdown → существующий semantic corpus/index контракт."""
from copy import deepcopy

import pytest

from atlas.documents.corpus_adapter import capture_document_corpus
from atlas.documents.models import DocumentError
from atlas.documents.store import DocumentStore
from atlas.semantic_index import IndexDatabase


@pytest.fixture
def captured(tmp_path):
    """Строит настоящий process-backed Markdown snapshot и закрывает writer."""
    project = tmp_path / 'project'
    (project / 'docs').mkdir(parents=True)
    text = ('# Currency\n\nCurrency conversion rules.\n\n'
            '[source](../src/Rate.cs#L12)\n[second](other.md)\n'
            '[remote](https://example.invalid/no-fetch)\n'
            '![image](../src/Image.cs)\n\nUnlinked Calculator means no association.\n')
    (project / 'docs' / 'rules.md').write_text(text, encoding='utf-8')
    (project / 'docs' / 'other.md').write_text('# Other\n\nHistorical captured text.', encoding='utf-8')
    store = DocumentStore(tmp_path / 'artifacts', project, 'project')
    job = store.ensure('documentation', ['docs/rules.md', 'docs/other.md'])
    result = store.wait(job['id'], 20)
    assert result['status'] == 'completed', result
    manifest = store.snapshot('documentation')
    root = store.root
    store.close()
    graph = {'metadata': {'snapshotId': 'code-snapshot'}, 'nodes': [
        {'id': 'rate-method', 'file': 'src/Rate.cs'},
        {'id': 'image-method', 'file': 'src/Image.cs'},
        {'id': 'unlinked-calculator', 'file': 'src/Calculator.cs'}]}
    return root, manifest, graph, project, text


def test_adapter_uses_immutable_capture_after_original_deletion_and_no_parser(captured, monkeypatch):
    """Оба pinned snapshot IDs и explicit links переживают удаление исходников."""
    from markdown_it import MarkdownIt
    root, manifest, graph, project, text = captured
    for path in (project / 'docs').iterdir():
        path.unlink()
    monkeypatch.setattr(MarkdownIt, 'parse', lambda *args, **kwargs: pytest.fail('Markdown parse during export'))
    first = capture_document_corpus(root, manifest, graph, 'code-snapshot')
    second = capture_document_corpus(root, manifest, graph, 'code-snapshot')
    assert first == second
    assert first['schemaVersion'] == 2
    assert first['snapshotId'] == 'code-snapshot'
    assert first['documentSnapshotId'] == manifest['snapshotId']
    document = next(document for document in first['documents'] if document['file'] == 'docs/rules.md')
    assert document['text'] == text
    assert document['nodeIds'] == []
    assert len(document['sourceLinks']) == 2
    source, other = document['sourceLinks']
    assert source['file'] == 'src/Rate.cs' and source['line'] == 12
    assert source['nodeIds'] == ['rate-method'] and source['scope'] == 'file'
    assert source['documentLine'] == 5
    assert other['documentId'] == next(document['id'] for document in first['documents']
                                       if document['file'] == 'docs/other.md')
    assert all('unlinked-calculator' not in link['nodeIds'] for link in document['sourceLinks'])
    assert all(chunk['documentSnapshotId'] == manifest['snapshotId'] for chunk in first['chunks'])


def test_adapter_limits_are_deterministic_and_manifest_cannot_traverse_storage(captured):
    """Budgets понижаются явно; прочие профили не становятся Markdown corpus."""
    root, manifest, graph, project, _ = captured
    limited = capture_document_corpus(root, manifest, graph, 'code-snapshot', limits={'maxChunks': 1})
    assert len(limited['chunks']) == 1 and limited['truncated']
    assert limited['warnings']
    with pytest.raises(DocumentError) as error:
        capture_document_corpus(root, manifest, graph, 'other-code-snapshot')
    assert error.value.code == 'SNAPSHOT_MISMATCH'
    altered = deepcopy(manifest)
    altered['documents'][0]['blobHash'] = '../../outside'
    with pytest.raises(DocumentError) as error:
        capture_document_corpus(root, altered, graph, 'code-snapshot')
    assert error.value.code == 'INVALID_ARTIFACT'
    altered = deepcopy(manifest)
    for binding in altered['documents']:
        binding['provider'] = 'document-pdf-basic'
    assert capture_document_corpus(root, altered, graph, 'code-snapshot')['documents'] == []


class _Embedding:
    """Deterministic small local vectors exercise the actual LanceDB schema-2 path."""

    def identity(self):
        """Return an observable test-only embedding profile."""
        return {'name': 'document-corpus-test', 'dimensions': 3, 'fingerprint': 'document-corpus-test-v1'}

    def split_text(self, text, max_tokens=126):
        """Keep fixture-sized chunks intact; production tokenizer owns real token limits."""
        return [{'text': text, 'start': 0, 'end': len(text), 'tokenCount': 3}]

    def embed(self, texts):
        """Map fixture currency content to one direction without loading a model."""
        return [self.query(text) for text in texts]

    def query(self, text):
        """Give currency text a separate nonzero vector from the second document."""
        return [1.0, 0.0, 0.1] if 'currency' in text.lower() else [0.0, 1.0, 0.1]


def test_captured_corpus_builds_existing_semantic_index_and_explicit_source_references(captured, tmp_path):
    """Новый adapter реально совместим с persisted docs/chunks/source refs, без ONNX claim."""
    root, manifest, graph, project, _ = captured
    corpus = capture_document_corpus(root, manifest, graph, 'code-snapshot')
    index = IndexDatabase(tmp_path / 'semantic-artifacts', _Embedding())
    result = index.build('project', 'code-snapshot', corpus, lambda: None)
    assert result['schemaVersion'] == 2 and result['documentCount'] == 2
    hits = index.search('project', 'code-snapshot', 'currency', limit=2)
    assert hits['hits'][0]['file'] == 'docs/rules.md'
    links = index.documents_for_nodes('project', 'code-snapshot', ['rate-method'])
    assert len(links) == 1
    assert links[0]['file'] == 'docs/rules.md'
