"""Explicit HTTP bridge: captured Markdown search не заменяет code corpus."""
import json
from pathlib import Path

from pydantic import BaseModel, Field

from .documents.models import require


class SearchScope(BaseModel):
    """Оба snapshot обязательны; latest fallback в поиске отсутствует."""
    projectId: str = Field(min_length=1, max_length=128)
    codeSnapshotId: str = Field(min_length=1, max_length=256)
    snapshotId: str = Field(min_length=1, max_length=256)
    model_config = {'extra': 'forbid'}


class SearchBuild(SearchScope):
    """Build запускается только явным POST с ограниченной boolean force настройкой."""
    force: bool = Field(default=False, strict=True)


class SearchQuery(SearchScope):
    """Top-k query ограничен длиной, числом целых hits и общим размером ответа."""
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=50, strict=True)


def _bounded(result):
    """Исключает только целые hits; identity/provenance и отдельные поля не обрезаются."""
    result = dict(result)
    if 'hits' in result:
        result['hits'] = list(result['hits'])
    clipped = False
    while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 64000 and result.get('hits'):
        result['hits'].pop()
        clipped = True
        result['responseTruncated'] = True
    require(len(json.dumps(result, ensure_ascii=False).encode('utf-8')) <= 64000,
            'RESPONSE_LIMIT', 'Search metadata превышает предел ответа.')
    if clipped:
        result['responseTruncated'] = True
    return result


def register_document_search_routes(app, projects, documents, search):
    """Компонует один search service; routes не читают исходники и не вызывают parsers."""
    def selected(map_id, project_id, snapshot_id):
        """Проверяет registered project и document ownership до index/corpus доступа."""
        require(isinstance(project_id, str) and bool(project_id), 'PROJECT_REQUIRED',
                'Search bridge требует зарегистрированный проект.')
        binding = documents.binding(map_id, project_id)
        with projects.lock:
            projects._project(project_id)
        store = documents._store(binding)
        return binding, store, store.snapshot(map_id, snapshot_id)

    @app.post('/api/document-maps/{map_id}/search-index')
    def build(map_id: str, body: SearchBuild):
        """Захватывает только сохранённый corpus и actual pinned code graph по explicit POST."""
        binding, store, manifest = selected(map_id, body.projectId, body.snapshotId)
        root, graph = projects.comparison_snapshot(body.projectId)
        projects.assert_snapshot(body.projectId, body.codeSnapshotId)
        require(graph.get('metadata', {}).get('snapshotId') == body.codeSnapshotId,
                'SNAPSHOT_MISMATCH', 'Code graph сменился; выберите актуальный snapshot.', 409)
        require(Path(root).resolve() == Path(binding['root']).resolve(), 'ROOT_SCOPE_MISMATCH',
                'Captured документы относятся к другому корню проекта.', 403)
        with store.snapshot_lease(map_id, body.snapshotId) as pinned:
            result = search.build(store.root, pinned, graph, body.projectId, body.codeSnapshotId, force=body.force)
        return _bounded(result)

    @app.get('/api/document-maps/{map_id}/search-index')
    def status(map_id: str, projectId: str, codeSnapshotId: str, snapshotId: str):
        """Читает точную published пару; current code graph и модель не загружаются."""
        selected(map_id, projectId, snapshotId)
        return _bounded(search.status(projectId, codeSnapshotId, map_id, snapshotId))

    @app.post('/api/document-maps/{map_id}/search')
    def query(map_id: str, body: SearchQuery):
        """Ищет existing index; отсутствие индекса/модели не запускает build или download."""
        selected(map_id, body.projectId, body.snapshotId)
        return _bounded(search.search(body.projectId, body.codeSnapshotId, map_id, body.snapshotId,
                                      body.query, limit=body.limit))
