"""File/line context over existing permanent or explicitly leased temporary snapshots."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .checkout_maps_api import SessionId
from .mcp_response_budget import json_bytes
from .position_context import PositionContextError, PositionContextInput, build_position_context
from .store import StoreError


MAX_RESPONSE_BYTES = 64_000


def read_position_context(store, project_id, body, *, session_id=None):
    """Read one pinned host snapshot; publication and final envelope are checked after analysis.

    The store and optional session are selected by the host, never by request graph
    data. A private caller must hold its access lease until this function returns.
    Source and graph freshness are separate: the engine reports source uncertainty,
    while any publication race rejects the whole response with the existing 409.
    """
    store.assert_snapshot(project_id, body.snapshotId)
    root, graph = store.comparison_snapshot(project_id)
    if graph.get('metadata', {}).get('snapshotId') != body.snapshotId:
        raise StoreError('Карта обновилась. Повторите запрос с актуальным snapshotId.', 409)
    result = build_position_context(root, graph, body)
    store.assert_snapshot(project_id, body.snapshotId)
    response = {**result, 'projectId': project_id}
    if session_id is not None:
        response.update(checkoutSessionId=session_id, temporary=True)
    if json_bytes(response) > MAX_RESPONSE_BYTES:
        raise StoreError('Position context exceeds 64000 bytes; reduce depth/maxNodes/maxEdges.', 413)
    return response


def register_position_context_routes(app: FastAPI, store, sessions) -> None:
    """Expose the same position contract in two scopes without creating or rebuilding maps."""
    @app.exception_handler(PositionContextError)
    async def position_error(request, error):
        """Keep controlled selection/source/budget failures explicit at the HTTP boundary."""
        return JSONResponse({'error': str(error), 'code': error.code}, status_code=error.status)

    @app.post('/api/projects/{project_id}/position-dependencies')
    def permanent(project_id: str, body: PositionContextInput):
        """Read the explicit permanent project; never change its grouping or source files."""
        return read_position_context(store, project_id, body)

    @app.post('/api/checkout-sessions/{session_id}/position-dependencies')
    def temporary(session_id: SessionId, body: PositionContextInput):
        """Pin this private store; only a fully bounded successful read renews its TTL."""
        with sessions.access(session_id) as entry:
            return read_position_context(entry.store, entry.project_id, body, session_id=session_id)
