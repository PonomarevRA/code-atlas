"""Настоящий Graphistry CPU: веса, изоляты и явные ошибки без fallback."""

from copy import deepcopy
import subprocess

from fastapi.testclient import TestClient
import networkx as nx
import pytest

from atlas import graphistry_provider, store
from atlas.server import create_app
from test_grouping_cache import legacy_store


def weighted_fixture(with_weights=True):
    """Создаёт полный граф, где большие внутренние веса должны выделить две пары."""
    graph = nx.Graph()
    graph.add_nodes_from(["a", "b", "c", "d", "isolated"])
    for source, target in [("a", "b"), ("a", "c"), ("a", "d"), ("b", "c"), ("b", "d"), ("c", "d")]:
        weight = 100.0 if with_weights and (source, target) in [("a", "b"), ("c", "d")] else 1.0
        graph.add_edge(source, target, weight=weight)
    return graph


def partition_set(partitions):
    """Сравнивает состав сообществ независимо от порядка результата."""
    return {frozenset(group) for group in partitions}


def test_real_graphistry_cpu_uses_weights_and_preserves_isolate():
    """PyGraphistry учитывает веса: две сильные пары вместо одной однородной клики."""
    graph = weighted_fixture()
    before = deepcopy(nx.node_link_data(graph, edges="edges"))
    weighted = graphistry_provider.partitions(graph)
    unweighted = graphistry_provider.partitions(weighted_fixture(False))
    assert partition_set(weighted) == {frozenset("ab"), frozenset("cd"), frozenset(["isolated"])}
    assert partition_set(unweighted) == {frozenset("abcd"), frozenset(["isolated"])}
    assert nx.node_link_data(graph, edges="edges") == before


def test_graphistry_handles_empty_and_edgeless_graphs():
    """Пустая карта и отдельные определения не теряются в CPU-провайдере."""
    assert graphistry_provider.partitions(nx.Graph()) == []
    isolated = nx.Graph()
    isolated.add_nodes_from(["first", "second"])
    assert partition_set(graphistry_provider.partitions(isolated)) == {frozenset(["first"]), frozenset(["second"])}


@pytest.mark.parametrize("failure", ["timeout", "missing-dependency"])
def test_graphistry_failures_return_503_without_fallback(tmp_path, monkeypatch, failure):
    """Неудачный дочерний процесс даёт диагностируемую ошибку, а не чужую группировку."""
    state, project_id, snapshot_path, graph = legacy_store(tmp_path)
    graph["nodes"].append({"id": "second", "label": "Second", "file": "second.py"})
    graph["edges"].append({"source": "first", "target": "second", "relation": "calls"})
    store.write_json(snapshot_path, graph)
    state.close()
    calls = []

    def failed_child(command, *args, **kwargs):
        """Имитирует только процесс Graphistry после проверки фактической команды."""
        calls.append(command)
        assert "atlas.offline_runner" in command
        assert "atlas.graphistry_worker" in command
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, timeout=kwargs.get("timeout", 1))
        return subprocess.CompletedProcess(command, 1, "", "ModuleNotFoundError: No module named 'graphistry'")

    monkeypatch.setattr(graphistry_provider.subprocess, "run", failed_child)
    with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        response = client.get(f"/api/projects/{project_id}/graph", params={"algorithm": "graphistry"})
        assert response.status_code == 503, response.text
        assert response.json().get("error")
    assert len(calls) == 1
    assert not list(snapshot_path.parent.glob("atlas-grouped-*-graphistry.json"))


@pytest.mark.parametrize("output", [
    "not-json",
    '[["a","b"],["c","d"]]',
    '[["a","b"],["c","d","isolated","unknown"]]',
    '[["a","b"],["a","c","d","isolated"]]',
    '[["a","b"],[],["c","d","isolated"]]',
])
def test_invalid_child_partition_is_rejected_without_losing_facts(monkeypatch, output):
    """Повреждённый ответ не допускает потерю, добавление или дублирование узлов."""
    def invalid_child(command, *args, **kwargs):
        """Возвращает некорректное разбиение от успешно завершившегося процесса."""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(graphistry_provider.subprocess, "run", invalid_child)
    with pytest.raises(graphistry_provider.GraphistryError):
        graphistry_provider.partitions(weighted_fixture())
