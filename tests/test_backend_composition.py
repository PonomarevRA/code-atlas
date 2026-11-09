"""Feature routes keep existing contracts and always release owned background services."""
import inspect

from fastapi.testclient import TestClient
import pytest

from atlas import server


@pytest.mark.parametrize('failures', [('remote',), ('store',), ('remote', 'store')])
def test_shutdown_releases_every_service_and_preserves_first_error(tmp_path, monkeypatch, failures):
    """A failing close cannot leak the database or replace the first failure with a later one."""
    app = server.create_app(tmp_path)
    closed = []
    errors = {name: RuntimeError(name + ' shutdown failure') for name in failures}
    services = [('remote', app.state.remote_repositories), ('store', app.state.store),
                ('semantic', app.state.semantic)]
    for name, service in services:
        original = service.close

        def close(name=name, original=original):
            """Release the real fixture resources before modelling a shutdown error."""
            original()
            closed.append(name)
            if name in errors:
                raise errors[name]

        monkeypatch.setattr(service, 'close', close)
    with pytest.raises(RuntimeError) as result:
        with TestClient(app, base_url='http://127.0.0.1:8765') as client:
            assert client.get('/api/health').status_code == 200
    assert closed == ['remote', 'store', 'semantic']
    assert result.value is errors[failures[0]]
    if len(failures) > 1:
        assert 'store shutdown failure' in result.value.__notes__[0]


def test_feature_routes_are_registered_once_and_heavy_work_stays_off_event_loop(tmp_path):
    """Registration preserves methods/paths while sync analysis remains a worker-thread endpoint."""
    app = server.create_app(tmp_path)
    try:
        routes = {route.path: route for route in app.routes if route.path.startswith('/api/')}
        for path, module in [
            ('/api/projects/{project_id}/entrypoints', 'atlas.graph_analysis_api'),
            ('/api/projects/{project_id}/execution-path', 'atlas.graph_analysis_api'),
            ('/api/projects/{project_id}/node-wiki', 'atlas.semantic_api'),
            ('/api/projects/{project_id}/semantic-search', 'atlas.semantic_api'),
            ('/api/documentation-mcp/tools', 'atlas.documentation_api'),
        ]:
            route = routes[path]
            assert route.endpoint.__module__ == module
            if module != 'atlas.documentation_api':
                assert not inspect.iscoroutinefunction(route.endpoint)
        keys = [(route.path, method) for route in app.routes if hasattr(route, 'methods')
                for method in route.methods]
        assert len(keys) == len(set(keys))
        assert routes['/api/projects/{project_id}/entrypoints'].methods == {'GET'}
        assert routes['/api/projects/{project_id}/execution-path'].methods == {'POST'}
    finally:
        app.state.remote_repositories.close()
        app.state.store.close()
        app.state.semantic.close()
