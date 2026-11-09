"""Real LanceDB wiki distinguishes provenance, actual graph edges and semantic recommendations."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from atlas.semantic_index import IndexDatabase
from atlas.store import StoreError
from atlas.wiki_service import WikiService
from test_semantic_index import FakeEmbedding


class WikiEmbedding(FakeEmbedding):
    """Deterministic embeddings keep recommendation mechanics observable, not model quality."""
    def available(self):
        return True


class SnapshotStore:
    """Changing the current snapshot during a request rejects stale captured content."""
    def __init__(self, graph):
        self.graph = graph
        self.snapshot = 'snapshot'

    def assert_snapshot(self, project_id, snapshot_id):
        if project_id != 'project' or snapshot_id != self.snapshot:
            raise StoreError('Snapshot changed', 409)

    def comparison_snapshot(self, project_id):
        return None, self.graph


def prepared(tmp_path):
    """Direct, neighbor and unrelated documentation live in one immutable generation."""
    graph = {
        'nodes': [{'id': 'selected', 'label': 'currency calculation', 'file': 'Calculator.cs', 'kind': 'method'},
                  {'id': 'neighbor', 'label': 'Load', 'file': 'Loader.cs', 'kind': 'method'},
                  {'id': 'unrelated', 'label': 'Other', 'file': 'Other.cs', 'kind': 'method'}],
        'edges': [{'source': 'selected', 'target': 'neighbor', 'relation': 'calls', 'confidence': 'INFERRED',
                   'source_file': 'Calculator.cs', 'source_location': 'L12'}],
        'metadata': {'snapshotId': 'snapshot'},
    }
    documents = []
    chunks = []
    for name, nodes, text in [('direct', ['selected'], '# Direct currency wiki\n' + 'Body text\n' * 4000),
                              ('neighbor', ['neighbor'], '# Loader documentation'),
                              ('semantic', ['unrelated'], '# Currency onboarding recommendation')]:
        document = {'id': 'document:' + name, 'title': name, 'text': text, 'format': 'markdown',
                    'kind': 'documentation', 'file': 'docs/' + name + '.md', 'line': 1, 'endLine': 5,
                    'sourceHash': name + '-hash', 'sourceKind': 'working-tree', 'capturedAt': 'captured',
                    'nodeIds': nodes, 'association': 'declaration', 'sourceLinks': [], 'truncated': False}
        documents.append(document)
        chunks.append({'id': 'chunk:' + name, 'documentId': document['id'], 'text': text[:200],
                       'kind': 'documentation', 'file': document['file'], 'line': 1, 'endLine': 5,
                       'sourceHash': document['sourceHash'], 'nodeIds': nodes, 'association': 'declaration'})
    database = IndexDatabase(tmp_path, WikiEmbedding())
    database.build('project', 'snapshot', {'documents': documents, 'chunks': chunks}, lambda: None)
    store = SnapshotStore(graph)
    service = WikiService(store, SimpleNamespace(database=database, embedder=database.embedder))
    return store, database, service, graph, documents


def test_three_sections_keep_direct_source_graph_and_similarity_distinct(tmp_path):
    store, database, service, graph, documents = prepared(tmp_path)
    before = deepcopy(graph)
    result = service.node_wiki('project', 'snapshot', 'selected')
    assert [document['id'] for document in result['directDocuments']] == ['document:direct']
    assert result['directDocuments'][0]['matches'][0]['association'] == 'declaration'
    assert result['directDocuments'][0]['matches'][0]['scope'] == 'symbol'
    assert [document['id'] for document in result['neighborDocuments']] == ['document:neighbor']
    edge = result['neighborDocuments'][0]['via'][0]['edge']
    assert edge['relation'] == 'calls' and edge['confidence'] == 'INFERRED'
    assert edge['source_location'] == 'L12'
    assert [document['id'] for document in result['recommendations']] == ['document:semantic']
    assert result['recommendations'][0]['reason'] == 'semantic-similarity'
    assert 'edge' not in result['recommendations'][0]
    assert graph == before and graph['edges'] == before['edges']
    assert 'table' not in result['index']


def test_captured_wiki_can_skip_all_model_and_vector_work(tmp_path, monkeypatch):
    """Документы и настоящие связи доступны без проверки модели и semantic поиска."""
    store, database, service, graph, _ = prepared(tmp_path)
    before = deepcopy(graph)

    def unexpected(*args, **kwargs):
        """Любой вход в tokenizer/model/vector работу нарушает режим документов."""
        raise AssertionError('Captured wiki touched embedding or vector search')

    for method in ('available', 'identity', 'split_text', 'query', 'embed'):
        monkeypatch.setattr(database.embedder, method, unexpected)
    monkeypatch.setattr(database, 'search', unexpected)
    result = service.node_wiki('project', 'snapshot', 'selected', include_recommendations=False)
    assert [document['id'] for document in result['directDocuments']] == ['document:direct']
    assert [document['id'] for document in result['neighborDocuments']] == ['document:neighbor']
    assert result['graphNeighbors'][0]['edge'] == graph['edges'][0]
    assert result['neighborDocuments'][0]['via'][0]['edge'] == graph['edges'][0]
    assert result['recommendations'] == []
    assert result['recommendationStatus'] == 'not-requested'
    assert result['queryTruncated'] is False
    assert result['warnings'] == []
    assert 'table' not in result['index']
    assert graph == before


@pytest.mark.parametrize('value', [0, 1, 'true', 'false', None, [], {}])
def test_recommendation_option_requires_boolean_before_reading(value):
    """Прямые service-вызовы не превращают строку или число в согласие на модель."""
    service = WikiService(None, SimpleNamespace(database=None, embedder=None))
    with pytest.raises(StoreError) as invalid:
        service.node_wiki('project', 'snapshot', 'selected', include_recommendations=value)
    assert invalid.value.status == 400


def test_document_pagination_reads_captured_text_without_model_or_files(tmp_path):
    store, database, service, graph, documents = prepared(tmp_path)
    original = documents[0]['text']
    database.embedder.identity = lambda: (_ for _ in ()).throw(AssertionError('Read loaded model'))
    database.embedder.available = lambda: False
    offset, parts = 0, []
    while True:
        page = service.document('project', 'snapshot', 'document:direct', offset, 1000)
        assert page['document']['sourceHash'] == 'direct-hash'
        assert page['textOffsetStart'] == offset
        parts.append(page['text'])
        if not page['hasMore']:
            assert page['nextOffset'] is None
            break
        offset = page['nextOffset']
    assert ''.join(parts) == original
    result = service.node_wiki('project', 'snapshot', 'selected')
    assert result['directDocuments'] and result['neighborDocuments']
    assert result['recommendationStatus'] == 'model-unavailable' and result['recommendations'] == []


def test_snapshot_race_invalid_input_and_unknown_ids_are_explicit(tmp_path):
    store, database, service, graph, _ = prepared(tmp_path)
    with pytest.raises(StoreError) as unknown_node:
        service.node_wiki('project', 'snapshot', 'missing')
    assert unknown_node.value.status == 404
    with pytest.raises(StoreError) as unknown_document:
        service.document('project', 'snapshot', '../outside')
    assert unknown_document.value.status == 404
    for offset, size in [(-1, 100), (True, 100), (999999, 100), (0, 12001)]:
        with pytest.raises(StoreError) as invalid:
            service.document('project', 'snapshot', 'document:direct', offset, size)
        assert invalid.value.status == 400
    original = database.document
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        store.snapshot = 'new'
        return value
    database.document = changed
    with pytest.raises(StoreError) as stale:
        service.document('project', 'snapshot', 'document:direct')
    assert stale.value.status == 409


@pytest.mark.parametrize('include_recommendations', [True, False])
def test_one_request_pins_generation_and_exact_file_links_remain_file_scope(tmp_path, include_recommendations):
    store, database, service, graph, _ = prepared(tmp_path)
    first = database.published_generation('project', 'snapshot')
    # A new generation may be published while this response reads the old immutable tables.
    original = database.documents_for_nodes
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        if kwargs['metadata']['corpusHash'] == first['corpusHash']:
            fresh = {'documents': [{'id': 'new-doc', 'title': 'New', 'text': 'New text', 'kind': 'documentation',
                                   'format': 'markdown', 'nodeIds': [], 'association': 'file',
                                   'sourceLinks': [{'file': 'Calculator.cs', 'scope': 'file', 'nodeIds': ['selected'],
                                                    'association': 'source-link', 'line': 20}]}], 'chunks': []}
            database.build('project', 'snapshot', fresh, lambda: None)
        return value
    database.documents_for_nodes = changed
    result = service.node_wiki('project', 'snapshot', 'selected', include_recommendations=include_recommendations)
    assert result['index']['corpusHash'] == first['corpusHash']
    assert result['directDocuments'][0]['id'] == 'document:direct'
    database.documents_for_nodes = original
    current = service.node_wiki('project', 'snapshot', 'selected')
    assert current['directDocuments'][0]['matches'][0]['scope'] == 'file'
    assert current['directDocuments'][0]['matches'][0]['association'] == 'source-link'


def test_file_association_is_explicit_and_long_context_is_token_bounded(tmp_path):
    store, database, service, graph, _ = prepared(tmp_path)
    graph['nodes'][0]['description'] = 'long context ' * 500
    documents = [{'id': 'file-doc', 'title': 'File overview', 'text': 'currency overview', 'kind': 'comment',
                  'file': 'Calculator.cs', 'nodeIds': [], 'fileNodeIds': ['selected'], 'association': 'file'}]
    chunks = [{'id': 'file-chunk', 'documentId': 'file-doc', 'text': 'currency overview', 'kind': 'comment'}]
    database.build('project', 'snapshot', {'documents': documents, 'chunks': chunks}, lambda: None)
    base_split = database.embedder.split_text
    database.embedder.split_text = lambda text, max_tokens: [{'text': text[:30], 'start': 0, 'end': 30, 'tokenCount': 10},
                                                            {'text': text[30:], 'start': 30, 'end': len(text), 'tokenCount': 100}]
    result = service.node_wiki('project', 'snapshot', 'selected')
    assert result['queryTruncated']
    assert len(database.embedder.queries[-1]) == 30
    assert result['directDocuments'][0]['matches'][0]['association'] == 'file'
    assert result['directDocuments'][0]['matches'][0]['scope'] == 'file'


def test_document_metadata_caps_are_explicit_and_do_not_change_capture(tmp_path):
    store, database, service, graph, _ = prepared(tmp_path)
    identities = ['node-' + str(index) for index in range(20)]
    document = {
        'id': 'bounded', 'text': 'captured text', 'nodeIds': identities,
        'fileNodeIds': identities, 'sourceLinks': [{'file': 'A.cs', 'nodeIds': identities}],
    }
    database.build('project', 'snapshot', {'documents': [document], 'chunks': []}, lambda: None)
    page = service.document('project', 'snapshot', 'bounded')
    metadata = page['document']
    assert len(metadata['nodeIds']) == 12 and metadata['nodeIdsTruncated']
    assert len(metadata['fileNodeIds']) == 12 and metadata['fileNodeIdsTruncated']
    assert len(metadata['sourceLinks'][0]['nodeIds']) == 12
    assert metadata['sourceLinks'][0]['nodeIdsTruncated']
    assert database.document('project', 'snapshot', 'bounded')['nodeIds'] == identities
