"""Структурная группировка сохраняет факты, файлы и реальные границы проектов."""
from copy import deepcopy
import json
import subprocess
import pytest
from atlas.grouping import enrich_graph
from atlas import graphistry_provider


def structure_fixture():
    """Два проекта с сильной внешней связью, цельным файлом и неоднозначным узлом без файла."""
    return {"nodes": [
        {"id": "manifest-a", "file": "Alpha/Alpha.csproj", "label": "Alpha"},
        {"id": "manifest-b", "file": "Beta/Beta.csproj", "label": "Beta"},
        {"id": "a1", "file": "Alpha/One.cs", "label": "First"},
        {"id": "a2", "file": "Alpha/One.cs", "label": "Second"},
        {"id": "b1", "file": "Alpha/Two.cs", "label": "Third"},
        {"id": "c1", "file": "Beta/Three.cs", "label": "Other"},
        {"id": "external", "file": "", "kind": "external", "label": "Shared"},
        {"id": "orphan", "file": "", "kind": "external", "label": "Orphan"},
    ], "edges": [
        {"source": "manifest-a", "target": "a1", "relation": "contains"},
        {"source": "manifest-b", "target": "c1", "relation": "contains"},
        {"source": "a1", "target": "b1", "relation": "calls"},
        {"source": "a2", "target": "c1", "relation": "calls"},
        {"source": "a1", "target": "external", "relation": "references"},
        {"source": "c1", "target": "external", "relation": "references"},
    ], "metadata": {}}


def test_structure_preserves_files_projects_and_inferred_ownership():
    """Файл не дробится, чужой проект не сливается, косвенное членство не меняет владельца."""
    source = structure_fixture()
    before = deepcopy(source)
    result = enrich_graph(source, "graphistry-structure")
    assert source == before
    assert result["edges"] == source["edges"]
    nodes = {node["id"]: node for node in result["nodes"]}
    assert set(nodes) == {node["id"] for node in source["nodes"]}
    assert nodes["a1"]["community"] == nodes["a2"]["community"]
    assert nodes["a1"]["community"] != nodes["c1"]["community"]
    assert nodes["external"]["codeProjectId"] == "__unassigned__"
    assert nodes["external"]["communityAssociation"] == "inferred"
    assert nodes["external"]["communityAssociationAmbiguous"] is True
    assert nodes["external"]["community"] == nodes["a1"]["community"]
    assert nodes["orphan"].get("communityAssociation") != "inferred"
    shuffled = deepcopy(source)
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()
    repeated = enrich_graph(shuffled, "graphistry-structure")
    assert {node["id"]: node["community"] for node in repeated["nodes"]} == {identifier: node["community"] for identifier, node in nodes.items()}
    assert sum(group["internalEdges"] for group in result["communities"]) + sum(edge["weight"] for edge in result["communityEdges"]) == len(source["edges"])
    switched = enrich_graph(result, "graphistry")
    assert all("communityAssociation" not in node and "communityAssociationAmbiguous" not in node for node in switched["nodes"])


def test_structure_batches_all_projects_in_one_offline_child(monkeypatch):
    """Разные проекты не запускают отдельный SDK-процесс; использована offline-обёртка."""
    calls = []
    original = graphistry_provider.subprocess.run
    def observed(command, *args, **kwargs):
        """Наблюдает настоящий дочерний запуск и его batch payload."""
        calls.append((command, json.loads(kwargs["input"])))
        return original(command, *args, **kwargs)
    monkeypatch.setattr(graphistry_provider.subprocess, "run", observed)
    enrich_graph(structure_fixture(), "graphistry-structure")
    assert len(calls) == 1
    command, payload = calls[0]
    assert "atlas.offline_runner" in command
    assert "atlas.graphistry_worker" in command
    assert len(payload["graphs"]) >= 2


@pytest.mark.parametrize("answer", [[], [[['unexpected']]], [[['a']], [['a']]]])
def test_invalid_batch_partitions_rejected(monkeypatch, answer):
    """Batch-протокол не допускает неверное число областей или чужие узлы."""
    import networkx as nx
    first = nx.Graph()
    first.add_node("a")
    second = nx.Graph()
    second.add_node("b")
    def invalid_worker(payload):
        """Возвращает повреждённый ответ сразу на несколько независимых областей."""
        return answer
    monkeypatch.setattr(graphistry_provider, "_run_worker", invalid_worker)
    with pytest.raises(graphistry_provider.GraphistryError):
        graphistry_provider.partitions_many([first, second])
