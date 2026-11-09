"""HTTP-проекция фактов сохраняет ошибки и проверку ревизии после выборки."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from atlas.graph_analysis_api import register_graph_analysis_routes
from atlas.store import StoreError
from test_grouping_cache import legacy_store


@pytest.mark.parametrize("route, extra", [("graph-node", {}),
                                         ("graph-neighbors", {"direction": "both", "limit": 40})])
def test_graph_context_routes_pin_revision_and_recheck_after_projection(tmp_path, monkeypatch, route, extra):
    """Новая публикация после чистой выборки даёт 409; обычные и неизвестные ID сохраняют контракт."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    app = FastAPI()

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        """Преобразует доменную ошибку так же, как основной composition root."""
        return JSONResponse(status_code=error.status, content={"detail": str(error)})

    register_graph_analysis_routes(app, state)
    payload = {"nodeId": "first", "snapshotId": "first", **extra}
    url = f"/api/projects/{project_id}/{route}"
    original = state.graph_context_snapshot

    def project_then_publish(*args, **kwargs):
        """Переключает карту между получением результата и проверкой API."""
        result = original(*args, **kwargs)
        next_path = graph_path.parent.parent / "next" / graph_path.name
        next_path.parent.mkdir(exist_ok=True)
        next_path.write_bytes(graph_path.read_bytes())
        with state.lock:
            state.projects[project_id]["_graph"] = str(next_path)
        return result

    try:
        with TestClient(app) as client:
            response = client.post(url, json=payload)
            assert response.status_code == 200 and response.json()["snapshotId"] == "first"
            assert client.post(url, json={**payload, "nodeId": "missing"}).status_code == 404
            assert client.post(url, json={**payload, "snapshotId": "stale"}).status_code == 409
            monkeypatch.setattr(state, "graph_context_snapshot", project_then_publish)
            assert client.post(url, json=payload).status_code == 409
    finally:
        state.close()
