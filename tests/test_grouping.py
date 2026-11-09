"""Приёмка группировки: факты графа, сообщество, агрегаты и стабильные координаты."""

from copy import deepcopy
import math
from pathlib import Path

from atlas.grouping import enrich_graph
from atlas.graphify_adapter import normalize_graph


def two_modules() -> dict:
    """Строит два плотных модуля с редкими направленными связями между ними."""
    nodes = []
    edges = []
    for module in ("billing", "identity"):
        for index in range(6):
            identifier = f"{module}-{index}"
            nodes.append({"id": identifier, "label": identifier, "kind": "function", "file": f"{module}/service.py", "description": "Факт из исходника"})
        for source in range(6):
            for target in range(source + 1, 6):
                edges.append({"source": f"{module}-{source}", "target": f"{module}-{target}", "relation": "calls", "confidence": "EXTRACTED"})
    edges.extend([
        {"source": "billing-0", "target": "identity-0", "relation": "calls", "confidence": "EXTRACTED"},
        {"source": "identity-1", "target": "billing-0", "relation": "references", "confidence": "INFERRED"},
    ])
    return {"nodes": nodes, "edges": edges, "metadata": {"provider": "Graphify", "nodeCount": len(nodes), "edgeCount": len(edges)}}


def assert_finite_positions(items):
    """Каждый отображаемый элемент получает конечные нормализованные координаты."""
    for item in items:
        for axis in ("x", "y"):
            assert math.isfinite(item[axis]), item
            assert -1.000001 <= item[axis] <= 1.000001, item


def test_two_modules_preserve_facts_and_exact_aggregate_counts():
    """Обнаруживает два модуля и считает все исходные связи ровно один раз."""
    original = two_modules()
    before = deepcopy(original)
    grouped = enrich_graph(original)
    assert original == before
    assert grouped["edges"] == original["edges"]
    assert len(grouped["nodes"]) == len(original["nodes"])
    originals = {node["id"]: node for node in original["nodes"]}
    for node in grouped["nodes"]:
        for key, value in originals[node["id"]].items():
            assert node[key] == value
    memberships = {node["id"]: node["community"] for node in grouped["nodes"]}
    billing = {memberships[f"billing-{index}"] for index in range(6)}
    identity = {memberships[f"identity-{index}"] for index in range(6)}
    assert len(billing) == len(identity) == 1
    assert billing.isdisjoint(identity)
    assert len(grouped["communities"]) == 2
    assert sum(group["nodeCount"] for group in grouped["communities"]) == 12
    assert sum(group["internalEdges"] for group in grouped["communities"]) == 30
    assert sum(group["externalEdges"] for group in grouped["communities"]) == 4
    assert len(grouped["communityEdges"]) == 1
    assert grouped["communityEdges"][0]["weight"] == 2
    assert_finite_positions(grouped["nodes"])
    assert_finite_positions(grouped["communities"])


def test_grouping_is_deterministic_despite_input_order():
    """Перестановка JSON-узлов и связей не меняет группы или раскладку."""
    original = two_modules()
    first = enrich_graph(original)
    reordered = deepcopy(original)
    reordered["nodes"].reverse()
    reordered["edges"].reverse()
    second = enrich_graph(reordered)
    first_nodes = {node["id"]: (node["community"], node["x"], node["y"]) for node in first["nodes"]}
    second_nodes = {node["id"]: (node["community"], node["x"], node["y"]) for node in second["nodes"]}
    assert first_nodes == second_nodes
    assert first["communities"] == second["communities"]
    assert first["communityEdges"] == second["communityEdges"]


def test_isolates_and_external_nodes_are_not_lost(tmp_path: Path):
    """Изолированные определения и внешние концы связей остаются доступны в группах."""
    raw = {"nodes": [{"id": "source"}, {"id": "isolated"}], "edges": [{"source": "source", "target": "external", "relation": "imports"}]}
    normalized = normalize_graph(raw, tmp_path / "graph.json")
    grouped = enrich_graph(normalized)
    assert {node["id"] for node in grouped["nodes"]} == {"source", "isolated", "external"}
    assert len(grouped["edges"]) == 1
    community_ids = {group["id"] for group in grouped["communities"]}
    assert all(node["community"] in community_ids for node in grouped["nodes"])
    assert_finite_positions(grouped["nodes"])
    assert_finite_positions(grouped["communities"])


def test_empty_graph_has_no_fabricated_groups():
    """Пустая карта корректно представляется без узлов и искусственных сообществ."""
    graph = enrich_graph({"nodes": [], "edges": [], "metadata": {}})
    assert graph["communities"] == []
    assert graph["communityEdges"] == []
    assert graph["nodes"] == []


def test_grouping_is_stable_across_process_hash_seeds():
    """Перезапуск Python не меняет координаты из-за порядка set внутри NetworkX."""
    import json
    import os
    import subprocess
    import sys

    script = """
import json
import sys
sys.path.insert(0, 'tests')
from test_grouping import two_modules
from atlas.grouping import enrich_graph
original = two_modules()
# Малые сообщества в большом графе задействуют фильтр subgraph NetworkX.
for i in range(20):
    original['nodes'].append({'id': 'isolate-' + str(i), 'kind': 'function'})
graph = enrich_graph(original)
print(json.dumps(graph, sort_keys=True))
"""
    outputs = []
    for seed in ("1", "2"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            env=environment, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]
