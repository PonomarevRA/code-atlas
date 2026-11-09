"""HTTP captured-search endpoints проверяют scopes до работы с корпусом и моделью."""
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from atlas.document_search import DocumentSearch
from atlas.document_search_api import register_document_search_routes, _bounded
from atlas.document_service import DocumentService
from atlas.documents.models import DocumentError
from atlas.store import StoreError
from test_grouping_cache import legacy_store
from test_document_search import LocalFixtureEmbedding


@pytest.fixture
def bridge(tmp_path):
    """Поднимает реальный store/index за in-process HTTP без TCP listener."""
    projects, project_id, graph_path, graph = legacy_store(tmp_path)
    source = tmp_path / 'guide.md'
    source.write_text('# Currency\n\nCurrency conversion source.', encoding='utf-8')
    documents = DocumentService(projects)
    created = documents.ensure(project_id=project_id, files=['guide.md'])
    binding = documents.binding(created['map']['id'], project_id)
    result = documents._store(binding).wait(created['job']['id'], 20)
    assert result['status'] == 'completed', result
    search = DocumentSearch(tmp_path / 'search-state', LocalFixtureEmbedding())
    app = FastAPI()

    @app.exception_handler(DocumentError)
    @app.exception_handler(StoreError)
    async def error(request, exception):
        """Use the same expected-error envelope as the composed Atlas HTTP app."""
        return JSONResponse({'code': getattr(exception, 'code', 'STORE_ERROR'), 'error': str(exception)}, status_code=exception.status)

    register_document_search_routes(app, projects, documents, search)
    try:
        yield TestClient(app), projects, documents, search, created['map']['id'], project_id, result['snapshotId'], source
    finally:
        search.close()
        documents.close()
        projects.close()


def test_search_http_explicit_build_reads_pinned_index_after_source_and_graph_change(bridge, monkeypatch):
    """Status/search не сравнивают latest graph и не подставляют новую document ревизию."""
    client, projects, documents, search, map_id, project_id, snapshot, source = bridge
    endpoint = '/api/document-maps/' + map_id
    scope = {'projectId': project_id, 'codeSnapshotId': 'first', 'snapshotId': snapshot}
    state = client.get(endpoint + '/search-index', params=scope)
    assert state.status_code == 200 and state.json()['status'] == 'not-indexed'
    not_built = client.post(endpoint + '/search', json={**scope, 'query': 'currency'})
    assert not_built.status_code == 409
    built = client.post(endpoint + '/search-index', json=scope)
    assert built.status_code == 200, built.text
    assert built.json()['index']['documentCount'] == 1
    source.unlink()
    monkeypatch.setattr(projects, 'comparison_snapshot', lambda *args: pytest.fail('live graph read during search'))
    monkeypatch.setattr(projects, 'assert_snapshot', lambda *args: pytest.fail('current graph check during historical search'))
    result = client.post(endpoint + '/search', json={**scope, 'query': 'currency'})
    assert result.status_code == 200, result.text
    assert result.json()['hits'][0]['text'].startswith('# Currency')
    assert result.json()['hits'][0]['documentSnapshotId'] == snapshot
    assert client.get(endpoint + '/search-index', params=scope).json()['status'] == 'ready'


def test_search_http_scope_rejections_happen_before_index_or_graph_access(bridge, monkeypatch):
    """Чужой project/map и отсутствующие pinned IDs не запускают extraction/search."""
    client, projects, documents, search, map_id, project_id, snapshot, source = bridge
    endpoint = '/api/document-maps/' + map_id
    scope = {'projectId': project_id, 'codeSnapshotId': 'first', 'snapshotId': snapshot}
    monkeypatch.setattr(search, 'build', lambda *args, **kwargs: pytest.fail('build before scope validation'))
    monkeypatch.setattr(search, 'search', lambda *args, **kwargs: pytest.fail('query before scope validation'))
    monkeypatch.setattr(projects, 'comparison_snapshot', lambda *args: pytest.fail('graph before scope validation'))
    for suffix, body in [('/search-index', {**scope, 'projectId': 'foreign'}),
                         ('/search', {**scope, 'projectId': 'foreign', 'query': 'currency'})]:
        assert client.post(endpoint + suffix, json=body).status_code == 403
    assert client.post(endpoint + '/search-index', json={'snapshotId': snapshot}).status_code == 422
    assert client.post(endpoint + '/search', json={**scope, 'query': 'currency', 'limit': 51}).status_code == 422


def test_search_http_real_missing_model_reports_unavailable_without_building(bridge, tmp_path, monkeypatch):
    """Missing preinstalled model остаётся явным HTTP 503, а не пустым successful index."""
    client, projects, documents, search, map_id, project_id, snapshot, source = bridge
    monkeypatch.delenv('ATLAS_EMBEDDING_CACHE', raising=False)
    unavailable = DocumentSearch(tmp_path / 'missing-model')
    search.embedder = unavailable.embedder
    scope = {'projectId': project_id, 'codeSnapshotId': 'first', 'snapshotId': snapshot}
    endpoint = '/api/document-maps/' + map_id
    state = client.get(endpoint + '/search-index', params=scope)
    assert state.json()['status'] == 'model-unavailable'
    built = client.post(endpoint + '/search-index', json=scope)
    assert built.status_code == 503 and built.json()['code'] == 'MODEL_UNAVAILABLE'
    assert not (search.data_dir / 'document-search').exists()
    unavailable.close()


def test_search_response_budget_preserves_whole_hits_and_scope():
    """A large top-k reply drops complete hits instead of breaking locators/citations."""
    result = {'projectId': 'p', 'codeSnapshotId': 'c', 'documentSnapshotId': 'd',
              'hits': [{'text': 'Ж' * 4000, 'sourceHash': 'a' * 64, 'sourceLocator': {'line': 1}} for _ in range(50)]}
    bounded = _bounded(result)
    assert bounded['projectId'] == 'p' and bounded['responseTruncated']
    assert 0 < len(bounded['hits']) < 50
    assert all(hit['text'] == 'Ж' * 4000 for hit in bounded['hits'])
    assert len(result['hits']) == 50
