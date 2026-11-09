"""MCP привязан к отображаемой ревизии, а не к последней карте другого клиента."""
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from atlas import server
from test_grouping_cache import legacy_store


@pytest.mark.parametrize('operation,body', [('describe', {'nodeId': 'first'}), ('query', {'question': 'First'})])
def test_mcp_checks_snapshot_before_process(tmp_path, monkeypatch, operation, body):
    """Нет token/старый token не запускают worker, успешный ответ подтверждает снимок."""
    state, project_id, snapshot, graph = legacy_store(tmp_path)
    raw = Path(graph["metadata"]["rawGraphPath"])
    state.close()
    calls = []

    async def reply(path, value):
        """Фиксирует реальный выбранный путь, не создавая MCP-процесс."""
        calls.append((path, value))
        return {'text': 'First', 'provider': 'fixture', 'tools': []}

    monkeypatch.setattr(server, 'describe_node', reply)
    monkeypatch.setattr(server, 'query_graph', reply)
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        url = f'/api/projects/{project_id}/{operation}'
        assert client.post(url, json=body).status_code == 422
        assert client.post(url, json={**body, 'snapshotId': 'stale'}).status_code == 409
        assert not calls
        result = client.post(url, json={**body, 'snapshotId': snapshot.parent.name})
        assert result.status_code == 200, result.text
        assert result.json()['snapshotId'] == snapshot.parent.name
        assert calls[0][0] == raw


def test_mcp_keeps_pinned_raw_when_next_snapshot_published(tmp_path, monkeypatch):
    """Публикация следующей карты после выбора raw-пути не подменяет ответ старого запроса."""
    state, project_id, snapshot, graph = legacy_store(tmp_path)
    raw = Path(graph["metadata"]["rawGraphPath"])
    state.close()
    app = server.create_app(tmp_path / 'state')
    captured = []

    async def reply(path, question):
        """Публикует другую ревизию в момент уже закреплённого MCP-запроса."""
        captured.append(path)
        updated = snapshot.parent.parent / 'next' / snapshot.name
        updated.parent.mkdir()
        updated.write_text(snapshot.read_text())
        with app.state.store.lock:
            app.state.store.projects[project_id]['_graph'] = str(updated)
        return {'text': 'pinned', 'tools': []}

    monkeypatch.setattr(server, 'query_graph', reply)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        payload = {'question': 'First', 'snapshotId': snapshot.parent.name}
        result = client.post(f'/api/projects/{project_id}/query', json=payload)
        assert result.status_code == 200
        assert result.json()['snapshotId'] == snapshot.parent.name
        assert captured == [raw]
        assert client.post(f'/api/projects/{project_id}/query', json=payload).status_code == 409
        assert captured == [raw]
