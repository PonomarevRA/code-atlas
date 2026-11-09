"""Покрытие доступно только для закреплённого проекта, без выполнения его тестов."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from fastapi.testclient import TestClient

from atlas import coverage_api, store
from atlas.server import create_app
from atlas.store import StoreError
from test_grouping_cache import legacy_store


@pytest.fixture
def coverage_client(tmp_path):
    """Публикует маленькую карту C#, сохраняя источник и snapshot для проверки read-only."""
    state, project_id, snapshot, original = legacy_store(tmp_path)
    project = tmp_path / 'source'
    project.mkdir()
    source = project / 'Calculator.cs'
    source.write_text('''class Calculator {
  public int Calculate(int value) {
    if (value > 0) return value;
    return 0;
  }
}
''')
    graph = {'nodes': [{'id': 'calculate', 'label': 'Calculate()', 'kind': 'method',
                        'file': 'Calculator.cs', 'line': 2}], 'edges': [],
             'metadata': original['metadata']}
    store.write_json(snapshot, graph)
    state.projects[project_id]['path'] = str(project)
    store.write_json(state.registry, state.projects)
    state.close()
    app = create_app(tmp_path / 'state')
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        yield client, app, project_id, snapshot, source


def test_coverage_endpoint_actual_analysis_and_snapshot_boundaries(coverage_client):
    """Без runtime-отчёта if остаётся неизвестным; stale и unknown не читают соседние проекты."""
    client, app, project_id, snapshot, source = coverage_client
    before_source, before_graph = source.read_bytes(), snapshot.read_bytes()
    route = f'/api/projects/{project_id}/test-coverage'
    body = {'snapshotId': snapshot.parent.name, 'nodeId': 'calculate'}
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['projectId'] == project_id
    assert data['snapshotId'] == snapshot.parent.name
    assert any(item['kind'] == 'if' and item['runtime']['status'] == 'unknown'
               for item in data['conditions'])
    assert source.read_bytes() == before_source
    assert snapshot.read_bytes() == before_graph
    assert client.post(route, json={**body, 'snapshotId': 'stale'}).status_code == 409
    assert client.post(route, json={**body, 'nodeId': 'missing'}).status_code == 404
    assert client.post('/api/projects/missing/test-coverage', json=body).status_code == 404


@pytest.mark.parametrize('extra', [
    {'command': 'dotnet test'}, {'path': '/tmp/project'}, {'snapshotId': ''},
    {'reportPaths': ['x'] * 9}, {'reportPaths': [1]}, {'reportPaths': ['']},
    {'reportPaths': ['x' * 1025]}, {'nodeId': ''},
])
def test_coverage_transport_limits_and_no_command_input(coverage_client, extra):
    """Форма API запрещает лишние команды и неограниченные значения до запуска анализатора."""
    client, app, project_id, snapshot, source = coverage_client
    response = client.post(f'/api/projects/{project_id}/test-coverage',
                           json={'snapshotId': snapshot.parent.name, **extra})
    assert response.status_code == 422


def test_coverage_snapshot_race_discards_result(coverage_client, monkeypatch):
    """Публикация карты во время анализа не выдаёт результат с прежними ID как актуальный."""
    client, app, project_id, snapshot, source = coverage_client

    def changed(project, revision):
        """Моделирует изменение snapshot после окончания вычислений."""
        raise StoreError('Карта изменилась', 409)

    monkeypatch.setattr(app.state.store, 'assert_snapshot', changed)
    response = client.post(f'/api/projects/{project_id}/test-coverage',
                           json={'snapshotId': snapshot.parent.name})
    assert response.status_code == 409


def test_coverage_concurrency_limit_releases_slot_after_failure(coverage_client, monkeypatch):
    """Два долгих анализа не блокируют сервер; завершение и ошибка освобождают допуск."""
    client, app, project_id, snapshot, source = coverage_client
    both_started, release = Event(), Event()
    lock, calls = Lock(), []

    def blocked(root, graph, revision, node, reports):
        """Задерживает только тестовый анализ, сохраняя независимость двух запросов."""
        with lock:
            calls.append(node)
            if len(calls) == 2:
                both_started.set()
        assert release.wait(5)
        if node == 'fail':
            raise coverage_api.TestCoverageError('Ошибка отчёта', 400)
        return {'snapshotId': revision}

    monkeypatch.setattr(coverage_api, 'build_test_coverage', blocked)
    route = f'/api/projects/{project_id}/test-coverage'
    body = {'snapshotId': snapshot.parent.name}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, route, json={**body, 'nodeId': 'fail'})
        second = pool.submit(client.post, route, json=body)
        try:
            assert both_started.wait(3)
            assert client.post(route, json=body).status_code == 429
        finally:
            release.set()
        assert first.result().status_code == 400
        assert second.result().status_code == 200
    assert client.post(route, json=body).status_code == 200
