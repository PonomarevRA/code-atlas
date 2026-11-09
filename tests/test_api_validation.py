"""Проверки локальной границы API и диагностируемых ошибок входных данных."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas.server import create_app


@pytest.fixture
def client(tmp_path: Path):
    """Запускает API с отдельным хранилищем и допустимым локальным Host."""
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1:8765") as instance:
        yield instance


def test_empty_store(client):
    """Новый экземпляр не наследует проекты пользователя."""
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert response.json()["projects"] == []
    assert response.json()["activeJob"] is None


@pytest.mark.parametrize("path", ["relative/path", "", "/definitely/missing/code-atlas-fixture"])
def test_invalid_project_paths_return_errors(client, path):
    """Ошибочные пути отклоняются до создания фоновой задачи."""
    response = client.post("/api/projects", json={"path": path})
    assert 400 <= response.status_code < 500
    assert response.json().get("error")
    assert client.get("/api/projects").json()["projects"] == []


def test_file_is_not_project(client, tmp_path):
    """Отдельный файл не принимается вместо каталога проекта."""
    source = tmp_path / "single.py"
    source.write_text("pass\n", encoding="utf-8")
    response = client.post("/api/projects", json={"path": str(source)})
    assert 400 <= response.status_code < 500
    assert response.json().get("error")


@pytest.mark.parametrize("route", ["/api/projects/unknown/graph", "/api/jobs/unknown"])
def test_missing_resources_return_json_errors(client, route):
    """Неизвестные идентификаторы дают безопасную ошибку, а не stack trace."""
    response = client.get(route)
    assert response.status_code == 404
    assert response.json().get("error")


def test_remote_origin_cannot_read_or_write(client):
    """Сторонняя страница не может использовать локальный файловый API."""
    headers = {"Origin": "https://untrusted.example"}
    assert client.get("/api/projects", headers=headers).status_code in (400, 403)
    assert client.post("/api/projects", json={"path": "/tmp"}, headers=headers).status_code in (400, 403)


def test_untrusted_host_is_rejected(client):
    """Недоверенный Host отклоняется, даже если запрос поступил локально."""
    assert client.get("/api/projects", headers={"Host": "untrusted.example"}).status_code in (400, 403)


def test_local_origin_can_read(client):
    """Собственная страница приложения имеет доступ к API."""
    response = client.get("/api/projects", headers={"Origin": "http://127.0.0.1:8765"})
    assert response.status_code == 200
