"""Реальное извлечение Python/C# и stdio MCP без исполнения кода проекта."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from atlas.graphify_adapter import GraphifyError, build_graph, normalize_graph
from atlas.mcp_bridge import _call_tools, describe_node, query_graph
from conftest import source_snapshot


def test_graphify_and_mcp_end_to_end(source_project: Path, tmp_path: Path):
    """Строит настоящие связи и читает описание из настоящего MCP-сервера."""
    before = source_snapshot(source_project)
    graph = build_graph(source_project, tmp_path / "graph-data")
    assert source_snapshot(source_project) == before
    assert graph["metadata"]["provider"] == "Graphify"
    assert graph["metadata"]["mode"] == "code-only"
    assert graph["edges"], "Graphify must recover fixture relationships"
    python_nodes = [node for node in graph["nodes"] if node["file"].endswith("service.py")]
    csharp_nodes = [node for node in graph["nodes"] if node["file"].endswith("Calculator.cs")]
    assert python_nodes, graph
    assert csharp_nodes, graph
    node = next(node for node in python_nodes if "normalize" in node["label"].lower())
    description = asyncio.run(describe_node(Path(graph["metadata"]["rawGraphPath"]), node["id"]))
    assert description["provider"] == "Graphify MCP"
    assert description["tools"] == ["get_node", "get_neighbors"]
    assert "normalize" in description["text"].lower()
    answer = asyncio.run(query_graph(Path(graph["metadata"]["rawGraphPath"]), "normalize"))
    assert answer["tools"] == ["query_graph"]
    assert answer["text"].strip()
    assert source_snapshot(source_project) == before


def test_data_directory_inside_source_is_rejected(source_project):
    """Не позволяет Graphify загрязнить индексируемое дерево своими данными."""
    before = source_snapshot(source_project)
    with pytest.raises(GraphifyError):
        build_graph(source_project, source_project / "state")
    assert source_snapshot(source_project) == before


def test_same_basename_in_two_languages_keeps_both_files(tmp_path):
    """Graphify сохраняет разные файлы с одинаковым stem без потери их функций."""
    source = tmp_path / "mixed-source"
    source.mkdir()
    (source / "shared.py").write_text("def python_value():\n    return 1\n", encoding="utf-8")
    (source / "shared.js").write_text("export function javascriptValue() { return 2; }\n", encoding="utf-8")
    before = source_snapshot(source)

    graph = build_graph(source, tmp_path / "mixed-graph")

    python_nodes = [node for node in graph["nodes"] if node["file"].endswith("shared.py")]
    javascript_nodes = [node for node in graph["nodes"] if node["file"].endswith("shared.js")]
    assert any("python_value" in node["label"] for node in python_nodes)
    assert any("javascriptValue" in node["label"] for node in javascript_nodes)
    python_ids = {node["id"] for node in python_nodes}
    javascript_ids = {node["id"] for node in javascript_nodes}
    assert python_ids.isdisjoint(javascript_ids)
    assert source_snapshot(source) == before


def test_normalization_materializes_external_nodes_and_preserves_direction(tmp_path):
    """Внешние зависимости сохраняются как узлы без выдуманного исходного файла."""
    raw = {
        "nodes": [{"id": "a", "label": "Alpha", "source_location": "L42-L51"}, {"id": "b"}],
        "links": [{"source": "a", "target": "b", "relation": "calls"}, {"source": "a", "target": "missing"}],
    }
    graph = normalize_graph(raw, tmp_path / "graph.json")
    assert graph["nodes"][0]["line"] == 42
    assert len(graph["edges"]) == 2
    assert graph["edges"][0]["source"] == "a"
    assert graph["edges"][0]["target"] == "b"
    assert graph["edges"][0]["relation"] == "calls"
    external = next(node for node in graph["nodes"] if node["id"] == "missing")
    assert external["kind"] == "external"
    assert external["file"] == ""
    assert external["line"] is None
    assert graph["metadata"]["nodeCount"] == 3


def test_documentation_nodes_are_notes_without_overriding_explicit_kinds(tmp_path):
    """Keep Graphify rationale evidence without presenting docstrings as code declarations."""
    raw = {'nodes': [
        {'id': 'doc', 'file_type': 'rationale', 'label': 'Explains a method'},
        {'id': 'explicit', 'file_type': 'rationale', 'kind': 'custom'},
        {'id': 'method', 'file_type': 'code', 'label': 'work()'},
    ], 'edges': [{'source': 'doc', 'target': 'method', 'relation': 'explains'}]}
    graph = normalize_graph(raw, tmp_path / 'graph.json')
    assert [node['kind'] for node in graph['nodes']] == ['note', 'custom', 'symbol']
    assert graph['nodes'][0]['file_type'] == 'rationale'
    assert graph['edges'][0]['relation'] == 'explains'


@pytest.mark.parametrize("raw", [{}, {"nodes": None}, {"nodes": [{}]}, {"nodes": [], "edges": [None]}])
def test_corrupt_graph_is_rejected(raw, tmp_path):
    """Повреждённые структуры завершаются ожидаемой ошибкой адаптера."""
    with pytest.raises(GraphifyError):
        normalize_graph(raw, tmp_path / "graph.json")


def test_api_build_and_description_use_real_local_graphify(source_project, tmp_path):
    """Проходит пользовательский путь HTTP → worker → Graphify → stdio MCP."""
    from fastapi.testclient import TestClient
    from atlas.server import create_app
    from test_jobs import add_project

    before = source_snapshot(source_project)
    with TestClient(create_app(tmp_path / "api-state"), base_url="http://127.0.0.1:8765") as client:
        project_id, job = add_project(client, source_project)
        assert job["status"] == "completed", job
        response = client.get(f"/api/projects/{project_id}/graph")
        assert response.status_code == 200
        graph = response.json()
        node = next(node for node in graph["nodes"] if "normalize" in node["label"].lower())
        response = client.post(f"/api/projects/{project_id}/describe", json={"nodeId": node["id"], "snapshotId": graph["metadata"]["snapshotId"]})
        assert response.status_code == 200, response.text
        assert response.json()["provider"] == "Graphify MCP"
        assert "normalize" in response.json()["text"].lower()
        missing = client.post(f"/api/projects/{project_id}/describe", json={"nodeId": "does-not-exist", "snapshotId": graph["metadata"]["snapshotId"]})
        assert missing.status_code == 404
        query = client.post(f"/api/projects/{project_id}/query", json={"question": "normalize", "snapshotId": graph["metadata"]["snapshotId"]})
        assert query.status_code == 200, query.text
        assert query.json()["tools"] == ["query_graph"]
    assert source_snapshot(source_project) == before


def test_external_dependency_counts_match_real_mcp(tmp_path):
    """UI и Graphify MCP считают одинаковый граф при отсутствующих концах связей."""
    raw = {
        "nodes": [{"id": "service", "label": "Service"}],
        "edges": [
            {"source": "service", "target": "external-library", "relation": "imports"},
            {"source": "external-caller", "target": "service", "relation": "calls"},
        ],
    }
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(raw), encoding="utf-8")
    normalized = normalize_graph(raw, graph_path)
    statistics = asyncio.run(_call_tools(graph_path, [("graph_stats", {})]))
    node_count = re.search(r"Nodes: (\d+)", statistics["text"])
    edge_count = re.search(r"Edges: (\d+)", statistics["text"])
    assert node_count and edge_count, statistics
    assert normalized["metadata"]["nodeCount"] == int(node_count.group(1)) == 3
    assert normalized["metadata"]["edgeCount"] == int(edge_count.group(1)) == 2
    assert {node["id"] for node in normalized["nodes"]} == {"service", "external-library", "external-caller"}
