"""Изолированный провайдер локальной группировки PyGraphistry без облачного API."""

import json
import math
import os
from pathlib import Path
import subprocess

import networkx as nx

from .runtime_paths import worker_executable, worker_creation_flags


class GraphistryError(RuntimeError):
    """PyGraphistry недоступен, превысил лимит или вернул неверное разбиение."""


def _worker_environment() -> dict[str, str]:
    """Передаёт только необходимые переменные запуска, без токенов и настроек SDK."""
    environment = {}
    for key in ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "LANG", "LC_ALL"):
        if key in os.environ:
            environment[key] = os.environ[key]
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    environment["PYTHONHASHSEED"] = "0"
    return environment


def _topology(graph: nx.Graph) -> dict:
    """Сериализует только идентификаторы и веса, без кода, путей и описаний."""
    if any(not isinstance(node, str) for node in graph.nodes):
        raise GraphistryError("Graphistry ожидает строковые идентификаторы узлов.")
    nodes = sorted(graph.nodes)
    edges = []
    for source, target, data in graph.edges(data=True):
        try:
            weight = float(data.get("weight", 1))
        except (TypeError, ValueError) as exc:
            raise GraphistryError("Graphistry ожидает числовые веса связей.") from exc
        if not math.isfinite(weight) or weight <= 0:
            raise GraphistryError("Graphistry ожидает положительные конечные веса связей.")
        left, right = sorted((source, target))
        edges.append([left, right, weight])
    return {"nodes": nodes, "edges": sorted(edges)}


def _validate_partition(value: object, expected: set[str]) -> list[set[str]]:
    """Проверяет полное покрытие узлов без неизвестных элементов и пересечений."""
    if not isinstance(value, list):
        raise GraphistryError("Graphistry вернул некорректный список сообществ.")
    seen = set()
    result = []
    for group in value:
        if not isinstance(group, list) or not group:
            raise GraphistryError("Graphistry вернул пустое или некорректное сообщество.")
        if any(not isinstance(node, str) for node in group):
            raise GraphistryError("Graphistry вернул некорректный идентификатор узла.")
        members = set(group)
        if len(members) != len(group) or seen.intersection(members):
            raise GraphistryError("Graphistry вернул повторяющиеся узлы.")
        seen.update(members)
        result.append(members)
    if seen != expected:
        raise GraphistryError("Разбиение Graphistry не совпадает с исходным набором узлов.")
    return result


def partitions(graph: nx.Graph) -> list[set[str]]:
    """Выполняет PyGraphistry CPU-группировку с запретом сети и лимитом 120 секунд."""
    payload = _topology(graph)
    response = _run_worker(payload)
    return _validate_partition(response, set(payload["nodes"]))


def partitions_many(graphs: list[nx.Graph]) -> list[list[set[str]]]:
    """Группирует независимые области одним изолированным процессом Graphistry."""
    topologies = [_topology(graph) for graph in graphs]
    response = _run_worker({"graphs": topologies})
    if not isinstance(response, list) or len(response) != len(topologies):
        raise GraphistryError("Graphistry вернул неверное число областей группировки.")
    return [_validate_partition(value, set(topology["nodes"]))
            for value, topology in zip(response, topologies)]


def _run_worker(payload: dict) -> object:
    """Передаёт топологии локальному процессу с запретом сети и общим тайм-аутом."""
    command = [worker_executable(), "-m", "atlas.offline_runner", "atlas.graphistry_worker"]
    try:
        result = subprocess.run(
            command, input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, timeout=120, check=False,
            cwd=Path(__file__).resolve().parent.parent, env=_worker_environment(),
            creationflags=worker_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise GraphistryError("Группировка Graphistry превысила лимит 120 секунд.") from exc
    except OSError as exc:
        raise GraphistryError(f"Не удалось запустить Graphistry: {exc}") from exc
    if result.returncode:
        details = result.stderr.strip()[-2000:]
        raise GraphistryError(f"Локальный Graphistry завершился с ошибкой: {details}")
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise GraphistryError("Graphistry вернул ответ, не являющийся JSON.") from exc
    return response
