"""Единый контракт провайдеров группировки и независимость сохранённых вариантов."""

from copy import deepcopy
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from atlas import store
from atlas.grouping import enrich_graph
from atlas.server import create_app
from test_grouping import assert_finite_positions, two_modules
from test_grouping_cache import legacy_store


PROVIDERS = ("louvain", "leiden", "greedy-modularity", "graphistry", "graphistry-structure", "natural")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_each_provider_preserves_facts_and_deterministic_partition(provider):
    """Любой провайдер сохраняет полный граф и воспроизводит разбиение при перестановке."""
    graph = two_modules()
    graph["nodes"].append({"id": "isolated", "label": "Isolated", "file": "isolated.py"})
    before = deepcopy(graph)
    grouped = enrich_graph(graph, provider)
    assert graph == before
    assert grouped["edges"] == graph["edges"]
    assert {node["id"] for node in grouped["nodes"]} == {node["id"] for node in graph["nodes"]}
    assert grouped["metadata"]["groupingProvider"] == provider
    assert sum(group["nodeCount"] for group in grouped["communities"]) == len(graph["nodes"])
    internal = sum(group["internalEdges"] for group in grouped["communities"])
    external = sum(edge["weight"] for edge in grouped["communityEdges"])
    assert internal + external == len(graph["edges"])
    assert_finite_positions(grouped["nodes"])
    assert_finite_positions(grouped["communities"])
    shuffled = deepcopy(graph)
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()
    second = enrich_graph(shuffled, provider)
    assert grouped["communities"] == second["communities"]
    assert grouped["communityEdges"] == second["communityEdges"]
    assert {node["id"]: node["community"] for node in grouped["nodes"]} == {node["id"]: node["community"] for node in second["nodes"]}


def test_provider_catalog_and_bad_provider_http_requests(tmp_path):
    """API публикует стабильные ID и отклоняет неизвестные/путевые значения."""
    state, project_id, _, _ = legacy_store(tmp_path)
    state.close()
    with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        # Startup initializes research storage. Measure endpoint side effects
        # after startup. The journal may record HTTP errors and shutdown; all other
        # files remain protected by exact byte hashes and inventory equality.
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                  for path in (tmp_path / 'state').rglob('*')
                  if path.relative_to(tmp_path / 'state').parts[0] != 'technical-logs'}
        catalog = client.get("/api/grouping-algorithms")
        assert catalog.status_code == 200
        assert catalog.json()["default"] == "graphistry"
        assert {item["id"] for item in catalog.json()["algorithms"]} == set(PROVIDERS)
        for item in catalog.json()["algorithms"]:
            assert item["label"] and item["description"]
        for bad in ("unknown", "../../outside", "/tmp/outside", "", "Leiden"):
            response = client.get(f"/api/projects/{project_id}/graph", params={"algorithm": bad})
            assert response.status_code == 400, response.text
            assert response.json().get("error")
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
             for path in (tmp_path / 'state').rglob('*')
                  if path.relative_to(tmp_path / 'state').parts[0] != 'technical-logs'}
    assert after == before


def test_each_provider_has_own_persistent_cache_and_source_invalidation(tmp_path, monkeypatch):
    """Алгоритмы не получают чужую карту; каждый кэш обновляется после изменения источника."""
    state, project_id, source_path, source = legacy_store(tmp_path)
    calls = []

    def counted_grouping(graph, algorithm="louvain"):
        """Наблюдает реальные вычисления по публичному контракту провайдера."""
        calls.append(algorithm)
        return enrich_graph(graph, algorithm)

    monkeypatch.setattr(store, "enrich_graph", counted_grouping)
    try:
        for provider in PROVIDERS:
            first = state.get_graph(project_id, provider)
            assert first["metadata"]["groupingProvider"] == provider
            assert state.get_graph(project_id, provider) == first
        assert calls == list(PROVIDERS)
    finally:
        state.close()
    reopened = store.ProjectStore(tmp_path / "state")
    try:
        for provider in PROVIDERS:
            assert reopened.get_graph(project_id, provider)["metadata"]["groupingProvider"] == provider
        assert calls == list(PROVIDERS)
        changed = deepcopy(source)
        changed["nodes"].append({"id": "new", "label": "New", "file": "new.py"})
        store.write_json(source_path, changed)
        for provider in PROVIDERS:
            assert len(reopened.get_graph(project_id, provider)["nodes"]) == 2
        assert calls == list(PROVIDERS) * 2
    finally:
        reopened.close()


@pytest.mark.parametrize("provider", ["leiden", "greedy-modularity", "graphistry", "graphistry-structure", "natural"])
def test_additional_providers_are_stable_across_processes(provider):
    """Перезапуск Python с другим hash seed сохраняет состав групп и координаты."""
    import json
    import os
    import subprocess
    import sys

    script = """
import json,sys
sys.path.insert(0,'tests')
from test_grouping import two_modules
from atlas.grouping import enrich_graph
print(json.dumps(enrich_graph(two_modules(),sys.argv[1]),sort_keys=True))
"""
    results = []
    for seed in ("1", "2"):
        result = subprocess.run(
            [sys.executable, "-c", script, provider],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, PYTHONHASHSEED=seed),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        results.append(json.loads(result.stdout))
    assert results[0] == results[1]
