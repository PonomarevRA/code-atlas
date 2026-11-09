"""Жизненный цикл снимков: конкуренция, ошибки и восстановление после перезапуска."""

import json
from pathlib import Path
from threading import Event
import time

from fastapi.testclient import TestClient
import pytest

from atlas.server import create_app
from atlas import store


def fake_build(project: Path, output: Path) -> dict:
    """Пишет небольшой совместимый снимок без вызова внешнего процесса."""
    raw_path = output / "graphify-out" / "graph.json"
    raw_path.parent.mkdir(parents=True)
    raw = {"nodes": [{"id": "first", "label": "First", "kind": "function", "file": "service.py"}], "edges": []}
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    return {**raw, "metadata": {"rawGraphPath": str(raw_path), "nodeCount": 1, "edgeCount": 0}}


def wait_for_job(client, job_id: str) -> dict:
    """Ожидает конечное состояние с коротким ограниченным polling."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.01)
    pytest.fail("Background job did not finish within 10 seconds")


def add_project(client, project: Path) -> tuple[str, dict]:
    """Добавляет проект и возвращает его ID вместе с завершённой задачей."""
    response = client.post("/api/projects", json={"path": str(project)})
    assert response.status_code == 202, response.text
    body = response.json()
    return body["project"]["id"], wait_for_job(client, body["job"]["id"])


def test_snapshot_survives_restart_and_failed_rebuild(tmp_path, source_project, monkeypatch):
    """Неудачная повторная сборка сохраняет прошлую рабочую карту на диске."""
    monkeypatch.setattr(store, "build_graph", fake_build)
    state = tmp_path / "state"
    with TestClient(create_app(state), base_url="http://127.0.0.1:8765") as client:
        project_id, job = add_project(client, source_project)
        assert job["status"] == "completed"
        # Health is a separate derivative job. Compare stable graph responses only
        # after its initial overlay, rather than racing a legitimate health update.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            refresh = client.get(f'/api/projects/{project_id}/code-health/refresh').json()
            if refresh['status'] == 'completed':
                break
            time.sleep(.01)
        else:
            pytest.fail(f'Initial health refresh did not finish: {refresh}')
        first_graph = client.get(f"/api/projects/{project_id}/graph").json()
        snapshot_path = Path(client.app.state.store.projects[project_id]['_graph'])
        captured_bytes = snapshot_path.read_bytes()
        assert "rawGraphPath" not in first_graph["metadata"]

        def failing_build(*args):
            """Имитирует ошибку парсера при обновлении существующего снимка."""
            raise RuntimeError("Fixture extraction failure")

        monkeypatch.setattr(store, "build_graph", failing_build)
        response = client.post(f"/api/projects/{project_id}/rebuild", json={})
        assert response.status_code == 202
        failed = wait_for_job(client, response.json()["job"]["id"])
        assert failed["status"] == "failed"
        assert "Fixture extraction failure" in failed["error"]
        assert snapshot_path.read_bytes() == captured_bytes
        assert client.get(f"/api/projects/{project_id}/graph").json() == first_graph
        assert client.get("/api/projects").json()["activeJob"] is None
    with TestClient(create_app(state), base_url="http://127.0.0.1:8765") as reopened:
        assert reopened.get(f"/api/projects/{project_id}/graph").json() == first_graph
        assert reopened.get("/api/projects").json()["projects"][0]["status"] == "failed"


def test_concurrent_build_rejected_and_worker_released(tmp_path, source_project, monkeypatch):
    """Одновременная сборка не создаёт второй проект и освобождает очередь после ошибки."""
    entered, release = Event(), Event()

    def slow_build(*args):
        """Держит worker до завершения проверки конкурирующего запроса."""
        entered.set()
        assert release.wait(5)
        raise RuntimeError("Intentional failure")

    monkeypatch.setattr(store, "build_graph", slow_build)
    with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        first = client.post("/api/projects", json={"path": str(source_project)}).json()
        try:
            assert entered.wait(5)
            response = client.post("/api/projects", json={"path": str(tmp_path)})
            assert response.status_code == 409
            assert len(client.get("/api/projects").json()["projects"]) == 1
        finally:
            release.set()
        assert wait_for_job(client, first["job"]["id"])["status"] == "failed"
        assert client.get("/api/projects").json()["activeJob"] is None


@pytest.mark.parametrize("content", ["not json", '{"nodes":[],"edges":[],"metadata":null}', '{"nodes":[null],"edges":[],"metadata":{}}'])
def test_corrupt_snapshot_returns_controlled_error(tmp_path, source_project, monkeypatch, content):
    """Повреждённый снимок предлагает пересборку вместо внутренней ошибки."""
    monkeypatch.setattr(store, "build_graph", fake_build)
    app = create_app(tmp_path / "state")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        project_id, _ = add_project(client, source_project)
        snapshot = Path(app.state.store.projects[project_id]["_graph"])
        snapshot.write_text(content, encoding="utf-8")
        response = client.get(f"/api/projects/{project_id}/graph")
        assert response.status_code == 409
        assert response.json().get("error")


def test_missing_snapshot_returns_controlled_error(tmp_path, source_project, monkeypatch):
    """Удалённый файл не приводит к чтению постороннего пути или ошибке сервера."""
    monkeypatch.setattr(store, "build_graph", fake_build)
    app = create_app(tmp_path / "state")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        project_id, _ = add_project(client, source_project)
        Path(app.state.store.projects[project_id]["_graph"]).unlink()
        assert client.get(f"/api/projects/{project_id}/graph").status_code == 409


def test_description_cannot_mix_revisions_during_publication(tmp_path, source_project, monkeypatch):
    """Публикация между проверкой узла и выбором raw-файла не смешивает ревизии."""
    from threading import Thread

    monkeypatch.setattr(store, "build_graph", fake_build)
    app = create_app(tmp_path / "state")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        project_id, _ = add_project(client, source_project)
        project_store = app.state.store
        original_raw = project_store.published_mcp_graph(project_id)
        # The initial health job reads the same canonical graph independently.
        # Finish that job before intercepting exactly the description read whose
        # publication lock this test exercises, rather than racing a second reader.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            refresh = client.get(f'/api/projects/{project_id}/code-health/refresh').json()
            if refresh['status'] == 'completed':
                break
            time.sleep(.01)
        else:
            pytest.fail('Initial health refresh did not settle before the publication race')
        # Lazy Graphify export has already warmed the canonical cache; force the
        # cold read whose publication lock is the subject of this race test.
        project_store._published_graph.clear()
        replacement = original_raw.with_name("replacement.json")
        replacement.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        original_get_graph = project_store._read_graph
        publication_attempts = []

        def competing_publisher():
            """Пытается опубликовать другую ревизию без ожидания блокировки."""
            acquired = project_store.lock.acquire(blocking=False)
            publication_attempts.append(acquired)
            if acquired:
                try:
                    project_store.projects[project_id]["_mcpGraph"] = str(replacement)
                finally:
                    project_store.lock.release()

        def intercepted_read(identifier):
            """Воспроизводит точное окно гонки после чтения нормализованного графа."""
            graph = original_get_graph(identifier)
            publisher = Thread(target=competing_publisher)
            publisher.start()
            publisher.join(timeout=2)
            assert not publisher.is_alive()
            return graph

        monkeypatch.setattr(project_store, "_read_graph", intercepted_read)
        selected_raw = project_store.description_snapshot(project_id, "first")
        assert publication_attempts == [False]
        assert selected_raw == original_raw
