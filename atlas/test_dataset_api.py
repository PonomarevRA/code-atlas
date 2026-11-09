"""HTTP access to the same declared test-input pages consumed by agent tools."""
from fastapi import Query

from .graph_context import GraphContextError
from .store import StoreError


def register_test_dataset_routes(app, store):
    """Register a snapshot-only reader; no analyzed code or provider is executed."""
    @app.get('/api/projects/{project_id}/test-datasets')
    def test_datasets(project_id: str, snapshotId: str = Query(min_length=1, max_length=128),
                      nodeId: str = Query(min_length=1, max_length=2000),
                      sourceId: str | None = Query(default=None, min_length=1, max_length=256),
                      cursor: str | None = Query(default=None, min_length=1, max_length=256),
                      limit: int = Query(default=20, ge=1, le=30)):
        """Return source catalogue or one bounded row page from a pinned test declaration."""
        try:
            return store.test_dataset_snapshot(project_id, snapshotId, nodeId, sourceId, cursor, limit)
        except GraphContextError as error:
            raise StoreError(str(error), error.status) from error
