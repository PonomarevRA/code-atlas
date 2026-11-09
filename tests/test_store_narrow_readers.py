"""Точечные читатели не копируют весь граф и сохраняют изоляцию ревизии."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest

from atlas import store
from atlas.graph_context import read_graph_node, read_graph_neighbors
from test_graph_context import graph_fixture
from test_grouping_cache import legacy_store
from test_jobs import fake_build


def test_point_readers_copy_only_selected_node_and_preserve_nested_isolation(tmp_path, monkeypatch):
    """Изменения вложенных фактов ответа не проникают в снимок или следующий ответ."""
    state, project_id, graph_path, graph = legacy_store(tmp_path)
    graph["nodes"][0]["attributeFacts"] = {"groups": [{"name": "original"}]}
    store.write_json(graph_path, graph)
    copies = []

    def bounded_copy(value):
        """Выявляет полную копию графа в запросе единственного узла."""
        assert not (isinstance(value, dict) and "nodes" in value and "edges" in value)
        copies.append(value)
        return deepcopy(value)

    monkeypatch.setattr(store, "deepcopy", bounded_copy)
    try:
        first = state.source_snapshot(project_id, "first", "first")[1]
        assert len(copies) == 1 and copies[0]["id"] == "first"
        first["attributeFacts"]["groups"][0]["name"] = "changed"
        second = state.source_snapshot(project_id, "first", "first")[1]
        assert second["attributeFacts"]["groups"][0]["name"] == "original"
        assert state.description_snapshot(project_id, "first") == Path(graph["metadata"]["rawGraphPath"])
        assert len(copies) == 2
        assert graph_path.read_text().find('"original"') >= 0
    finally:
        state.close()


def test_narrow_readers_reject_unknown_nodes_and_corrupt_replaced_file(tmp_path):
    """404 для неизвестного ID и 409 для битого снимка сохраняются после прогрева."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    try:
        state.source_snapshot(project_id, "first", "first")
        for read in (lambda: state.source_snapshot(project_id, "missing", "first"),
                     lambda: state.description_snapshot(project_id, "missing")):
            with pytest.raises(store.StoreError) as unknown:
                read()
            assert unknown.value.status == 404
        graph_path.write_text("{", encoding="utf-8")
        for read in (lambda: state.source_snapshot(project_id, "first", "first"),
                     lambda: state.description_snapshot(project_id, "first")):
            with pytest.raises(store.StoreError) as corrupt:
                read()
            assert corrupt.value.status == 409
    finally:
        state.close()


def test_narrow_reader_pins_node_while_publication_waits(tmp_path, monkeypatch):
    """Публикация не смешивает выбранный старый узел с путём следующей ревизии."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    next_graph = fake_build(tmp_path, graph_path.parent.parent / "next")
    next_graph["nodes"][0]["label"] = "Next revision"
    next_path = graph_path.parent.parent / "next" / graph_path.name
    store.write_json(next_path, next_graph)
    selected, release, publishing, published = Event(), Event(), Event(), Event()

    def paused_copy(value):
        """Останавливает копию выбранного узла в середине закреплённого чтения."""
        if isinstance(value, dict) and value.get("id") == "first":
            selected.set()
            assert release.wait(5)
        return deepcopy(value)

    def publish():
        """Имитирует атомарное переключение указателей после подготовки файлов."""
        publishing.set()
        with state.lock:
            state.projects[project_id].update(_graph=str(next_path),
                                             _rawGraph=next_graph["metadata"]["rawGraphPath"])
        published.set()

    monkeypatch.setattr(store, "deepcopy", paused_copy)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            reader = workers.submit(state.source_snapshot, project_id, "first", "first")
            assert selected.wait(5)
            publisher = workers.submit(publish)
            assert publishing.wait(5)
            assert not published.is_set()
            release.set()
            _, old_node = reader.result(timeout=5)
            publisher.result(timeout=5)
        assert old_node["label"] != "Next revision"
        with pytest.raises(store.StoreError) as stale:
            state.source_snapshot(project_id, "first", "first")
        assert stale.value.status == 409
        assert state.source_snapshot(project_id, "first", "next")[1]["label"] == "Next revision"
        assert state.description_snapshot(project_id, "first") == Path(next_graph["metadata"]["rawGraphPath"])
    finally:
        release.set()
        state.close()


def test_graph_context_preserves_payload_and_nested_isolation(tmp_path, monkeypatch):
    """Узел и соседство равны прежним чистым читателям, не копируя полный снимок."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    graph = graph_fixture()
    graph["metadata"]["snapshotId"] = "first"
    store.write_json(graph_path, graph)

    def deny_full_copy(value):
        """Запрещает прежнюю полную копию на пути ограниченной проекции."""
        raise AssertionError("ProjectStore must not deepcopy for graph context")

    monkeypatch.setattr(store, "deepcopy", deny_full_copy)
    try:
        expected = read_graph_node(graph, "method")
        result = state.graph_context_snapshot(project_id, "method", "first")
        assert result == expected
        result["node"]["attributeFacts"]["groups"][0]["name"] = "changed"
        assert state.graph_context_snapshot(project_id, "method", "first") == expected
        for direction in ("incoming", "outgoing", "both"):
            neighbors = state.graph_context_snapshot(project_id, "method", "first", direction, 2)
            assert neighbors == read_graph_neighbors(graph, "method", direction, 2)
            neighbors["edges"][0]["relation"] = "changed"
            assert state.graph_context_snapshot(project_id, "method", "first", direction, 2) == read_graph_neighbors(graph, "method", direction, 2)
        with pytest.raises(store.StoreError) as stale:
            state.graph_context_snapshot(project_id, "method", "stale")
        assert stale.value.status == 409
    finally:
        state.close()


def add_snapshot(state, tmp_path, project_id):
    """Добавляет независимый снимок без запуска фонового Graphify."""
    graph = fake_build(tmp_path, state.data_dir / project_id / "first")
    graph_path = state.data_dir / project_id / "first" / "atlas-graph.json"
    store.write_json(graph_path, graph)
    state.projects[project_id] = {"id": project_id, "name": project_id, "path": str(tmp_path),
                                  "_graph": str(graph_path), "_rawGraph": graph["metadata"]["rawGraphPath"]}
    return graph_path


def test_published_retention_evicts_least_recent_project_by_count(tmp_path, monkeypatch):
    """Прогретый точечный запрос обновляет LRU; вытесненный снимок остаётся на диске."""
    state, first_id, first_path, _ = legacy_store(tmp_path)
    second_path = add_snapshot(state, tmp_path, "second")
    add_snapshot(state, tmp_path, "third")
    monkeypatch.setattr(store, "MAX_PUBLISHED_PROJECTS", 2)
    try:
        state.source_snapshot(first_id, "first", "first")
        state.source_snapshot("second", "first", "first")
        state.description_snapshot(first_id, "first")
        state.source_snapshot("third", "first", "first")
        assert list(state._published_graph) == [first_id, "third"]
        assert first_path.is_file() and second_path.is_file()
        assert state.source_snapshot("second", "first", "first")[1]["id"] == "first"
        assert list(state._published_graph) == ["third", "second"]
    finally:
        state.close()


def test_published_retention_bounds_source_bytes_and_bypasses_oversize(tmp_path, monkeypatch):
    """Крупный граф возвращается корректно, сохраняя чужой прогретый снимок."""
    state, first_id, first_path, _ = legacy_store(tmp_path)
    second_path = add_snapshot(state, tmp_path, "second")
    budget = max(first_path.stat().st_size, second_path.stat().st_size)
    monkeypatch.setattr(store, "MAX_PUBLISHED_SOURCE_BYTES", budget)
    try:
        state.source_snapshot(first_id, "first", "first")
        state.source_snapshot("second", "first", "first")
        assert list(state._published_graph) == ["second"]
        oversized = add_snapshot(state, tmp_path, "large")
        graph = state._read_graph(oversized)
        graph["nodes"][0]["documentation"] = "large" * budget
        store.write_json(oversized, graph)
        for _ in range(2):
            result = state.source_snapshot("large", "first", "first")[1]
            assert result["documentation"] == "large" * budget
            assert list(state._published_graph) == ["second"]
        assert sum(entry[1][1] for entry in state._published_graph.values()) <= budget
    finally:
        state.close()


def test_published_retention_invalidates_rewrite_same_path_and_size(tmp_path):
    """Изменение mtime той же ревизии заменяет значение даже при прежнем размере JSON."""
    import os

    state, project_id, graph_path, graph = legacy_store(tmp_path)
    try:
        first = state.source_snapshot(project_id, "first", "first")[1]
        original_stamp = graph_path.stat()
        graph["nodes"][0]["label"] = "Z" * len(first["label"])
        store.write_json(graph_path, graph)
        assert graph_path.stat().st_size == original_stamp.st_size
        os.utime(graph_path, ns=(original_stamp.st_atime_ns, original_stamp.st_mtime_ns + 1_000_000))
        assert state.source_snapshot(project_id, "first", "first")[1]["label"] == graph["nodes"][0]["label"]
        assert len(state._published_graph) == 1
    finally:
        state.close()


def test_comparison_date_is_registry_date_and_cached_graph_is_unchanged(tmp_path):
    """Add registry provenance date without changing the published graph or its metadata."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    try:
        state.projects[project_id]["updatedAt"] = "2026-10-14T12:00:00Z"
        current_path = state._snapshot_path(project_id, "_graph")
        original = state._remember_graph(project_id, current_path)
        before = deepcopy(original)
        _, response = state.comparison_snapshot(project_id)
        assert response["metadata"]["graphUpdatedAt"] == "2026-10-14T12:00:00Z"
        response["nodes"][0]["label"] = "response only"
        assert state._remember_graph(project_id, current_path) == before
        assert original == before
    finally:
        state.close()
