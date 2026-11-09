"""Git comparison must reject a result if its captured map was replaced during work."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from atlas.graph_analysis_api import register_graph_analysis_routes
from atlas.store import StoreError
from test_grouping_cache import legacy_store


def test_comparison_rechecks_snapshot_after_git_work(tmp_path):
    """A rebuild between capture and response cannot publish highlights for an old map."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    app = FastAPI()

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        """Retain the production status contract without starting unrelated services."""
        return JSONResponse(status_code=error.status, content={'error': str(error)})

    def compare_then_publish(*args):
        """Deterministically reproduce publication while the Git command is running."""
        next_path = graph_path.parent.parent / 'next' / graph_path.name
        next_path.parent.mkdir(exist_ok=True)
        next_path.write_bytes(graph_path.read_bytes())
        with state.lock:
            state.projects[project_id]['_graph'] = str(next_path)
        return {'changedNodeIds': ['first']}

    register_graph_analysis_routes(app, state, comparison_builder=compare_then_publish)
    try:
        with TestClient(app) as client:
            response = client.post(f'/api/projects/{project_id}/compare',
                                   json={'baseRef': 'main', 'targetRef': 'HEAD'})
            assert response.status_code == 409
            assert 'error' in response.json()
    finally:
        state.close()
