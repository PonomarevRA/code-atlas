"""HTTP routes for the default grouping's compact map projections."""
from fastapi import Query

from .grouping_providers import DEFAULT_ALGORITHM
from .progressive_map import ProgressiveMap
from .store import StoreError


def register_progressive_map_routes(app, store):
    """Register a snapshot-pinned overview and scoped node pages."""
    service = ProgressiveMap(store)

    def check_algorithm(algorithm):
        """Reject grouping variants handled by the existing full map."""
        if algorithm != DEFAULT_ALGORITHM:
            raise StoreError('Обзор доступен для стандартной группировки.', 400)

    @app.get('/api/projects/{project_id}/map-overview')
    def map_overview(project_id: str, snapshotId: str = Query(min_length=1, max_length=128),
                     algorithm: str = DEFAULT_ALGORITHM):
        """Read the project and group overview for the current exact revision."""
        check_algorithm(algorithm)
        return service.read(project_id, snapshotId)

    @app.get('/api/projects/{project_id}/map-group')
    def map_group(project_id: str, snapshotId: str = Query(min_length=1, max_length=128),
                  algorithm: str = DEFAULT_ALGORITHM,
                  groupId: str | None = Query(default=None, min_length=1, max_length=512),
                  projectScope: str | None = Query(default=None, min_length=1, max_length=512),
                  offset: int = Query(default=0, ge=0, le=2_000_000),
                  limit: int = Query(default=100, ge=1, le=200)):
        """Read a bounded page from a group or subproject of that revision."""
        check_algorithm(algorithm)
        if groupId is None and projectScope is None:
            raise StoreError('Укажите группу или подпроект.', 400)
        return service.read(project_id, snapshotId, (groupId, projectScope, offset, limit))
