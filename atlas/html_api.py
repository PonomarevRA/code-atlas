"""Snapshot-only HTTP operations for CSS queries and passive HTML sketches."""
from pydantic import BaseModel, ConfigDict, Field
from .html_selectors import HtmlQueryError, query_html
from .html_preview import build_html_preview
from .store import StoreError


class HtmlNodeInput(BaseModel):
    """Select a raw HTML node on an exact snapshot."""
    model_config = ConfigDict(extra='forbid')
    snapshotId: str = Field(min_length=1, max_length=128)
    nodeId: str = Field(min_length=1, max_length=4096)


class HtmlQueryInput(HtmlNodeInput):
    """Add the bounded CSS selector and page to a pinned HTML node."""
    selector: str = Field(default='*', min_length=1, max_length=8192)
    offset: int = Field(default=0, ge=0, le=5000, strict=True)
    limit: int = Field(default=30, ge=1, le=50, strict=True)


def register_html_routes(app, store):
    """Регистрирует read-only анализ захваченного дерева; не обращается к root проекта."""
    @app.post('/api/projects/{project_id}/html-query')
    def html_query(project_id: str, body: HtmlQueryInput):
        """Проверяет ревизию до и после запроса, не смешивая опубликованные карты."""
        _, graph = store.comparison_snapshot(project_id)
        if graph['metadata']['snapshotId'] != body.snapshotId:
            raise StoreError('Карта обновилась. Загрузите актуальный снимок.', 409)
        try:
            result = query_html(graph, body.snapshotId, body.nodeId, body.selector, body.offset, body.limit)
        except HtmlQueryError as error:
            raise StoreError(str(error), error.status) from error
        store.assert_snapshot(project_id, body.snapshotId)
        return result


    @app.post('/api/projects/{project_id}/html-preview')
    def html_preview(project_id: str, body: HtmlNodeInput):
        """Read the pinned snapshot and return bounded display data without source markup."""
        try:
            return store._read_snapshot_analysis(project_id, body.snapshotId,
                lambda _root, graph: build_html_preview(graph, body.snapshotId, body.nodeId))
        except HtmlQueryError as error:
            raise StoreError(str(error), error.status) from error
