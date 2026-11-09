"""Контракт каталога исходников: только сохранённые raw-ID, без обхода рабочей папки."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from atlas import store
from atlas.server import create_app
from atlas.source_workspace import indexed_files
from test_grouping_cache import legacy_store


@pytest.fixture
def source_catalog(tmp_path):
    """Создаёт снимок с дубликатами, отсутствующими файлами и небезопасными путями."""
    state, project_id, snapshot, original = legacy_store(tmp_path)
    nodes = [
        {"id": "a-method", "file": "src/A.cs", "line": 2, "kind": "method"},
        {"id": "a-file", "file": "src/A.cs", "kind": "file"},
        {"id": "b-late", "file": "src/B.py", "line": 30},
        {"id": "b-first-z", "file": "src/B.py", "line": 4},
        {"id": "b-first-a", "file": "src/B.py", "line": 4},
        {"id": "unicode", "file": "Код/Ёж.py", "line": 1},
        {"id": "no-file"},
        {"id": "external", "file": "external.cs", "kind": "external"},
    ]
    for index, filename in enumerate([
        "../outside.cs", "a/../../outside.cs", "/absolute.cs",
        "C:/absolute.cs", "C:\\absolute.cs", "a:stream", "..\\outside.cs",
    ]):
        nodes.append({"id": f"unsafe-{index}", "file": filename, "line": 1})
    graph = {"nodes": nodes, "edges": [], "metadata": original["metadata"]}
    store.write_json(snapshot, graph)
    state.close()
    before = snapshot.read_bytes()
    with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        yield client, f"/api/projects/{project_id}/source-files", snapshot.parent.name, nodes
    assert snapshot.read_bytes() == before, "Listing must preserve the captured graph"


def test_source_files_are_deduplicated_stable_and_do_not_scan(source_catalog, monkeypatch):
    """Файл не обязан существовать; представитель выбирается по виду, строке и raw-ID."""
    client, route, snapshot_id, nodes = source_catalog

    def forbidden_scan(*args, **kwargs):
        """Каталог берёт пути из снимка, а не ищет файлы на диске."""
        raise AssertionError("Source catalog must not scan or regroup")

    monkeypatch.setattr(Path, "rglob", forbidden_scan)
    monkeypatch.setattr(os, "walk", forbidden_scan)
    monkeypatch.setattr(store, "enrich_graph", forbidden_scan)
    response = client.post(route, json={"snapshotId": snapshot_id, "limit": 100})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["snapshotId"] == snapshot_id
    assert data["files"] == [
        {"file": "src/A.cs", "nodeId": "a-file", "line": None},
        {"file": "src/B.py", "nodeId": "b-first-a", "line": 4},
        {"file": "Код/Ёж.py", "nodeId": "unicode", "line": 1},
    ]
    assert data["total"] == 3
    assert data["nextOffset"] is None
    assert data["truncated"] is False
    raw_ids = {node["id"] for node in nodes}
    assert all(item["nodeId"] in raw_ids for item in data["files"])


def test_source_file_filter_and_pages_are_stable(source_catalog):
    """Поиск использует регистронезависимый путь; страницы не повторяют файлы."""
    client, route, snapshot_id, _ = source_catalog
    body = {"snapshotId": snapshot_id, "query": "SRC/", "limit": 1}
    first = client.post(route, json=body).json()
    assert first["total"] == 2
    assert first["files"] == [{"file": "src/A.cs", "nodeId": "a-file", "line": None}]
    assert first["nextOffset"] == 1
    assert first["truncated"] is True
    second = client.post(route, json={**body, "offset": first["nextOffset"]}).json()
    assert second["files"] == [{"file": "src/B.py", "nodeId": "b-first-a", "line": 4}]
    assert second["nextOffset"] is None
    assert second["truncated"] is False
    unicode = client.post(route, json={"snapshotId": snapshot_id, "query": "ёж"}).json()
    assert [item["file"] for item in unicode["files"]] == ["Код/Ёж.py"]
    empty = client.post(route, json={"snapshotId": snapshot_id, "query": "absent"}).json()
    assert empty["files"] == [] and empty["total"] == 0
    assert empty["nextOffset"] is None and empty["truncated"] is False


def test_source_file_catalog_is_snapshot_scoped(source_catalog):
    """Старый снимок не подменяется текущим, чужой проект не раскрывает каталог."""
    client, route, snapshot_id, _ = source_catalog
    assert client.post(route, json={"snapshotId": "stale"}).status_code == 409
    assert client.post("/api/projects/unknown/source-files", json={"snapshotId": snapshot_id}).status_code == 404


def test_source_file_representative_prefers_any_positive_line_to_unknown():
    """Устаревшая очень большая строка остаётся известной позицией из снимка."""
    nodes = [
        {"id": "missing-line", "file": "stale.py"},
        {"id": "known-line", "file": "stale.py", "line": 10**12},
    ]
    for ordered in (nodes, list(reversed(nodes))):
        result = indexed_files({"nodes": ordered}, "snapshot")
        assert result["files"] == [{"file": "stale.py", "nodeId": "known-line", "line": 10**12}]


@pytest.mark.parametrize("change", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": "10"},
    {"offset": -1}, {"offset": 100001}, {"offset": True}, {"offset": "1"},
    {"snapshotId": ""}, {"snapshotId": "s" * 129},
    {"file": "/etc/passwd"}, {"path": "../outside"},
])
def test_source_file_catalog_rejects_unbounded_or_arbitrary_paths(source_catalog, change):
    """HTTP отклоняет лишние поля и неверные границы до чтения снимка."""
    client, route, snapshot_id, _ = source_catalog
    response = client.post(route, json={"snapshotId": snapshot_id, **change})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("script", ["test_source_reader.mjs", "test_source_workspace_view.mjs"])
def test_reader_browser_contracts(script):
    """Обычный pytest также запускает Node-контракты истории и интеграции без нового test stack."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name(script))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
