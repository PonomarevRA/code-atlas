"""Локальные взаимозаменяемые алгоритмы группировки одного взвешенного графа."""

from collections.abc import Callable

import networkx as nx

from .graphistry_provider import partitions as graphistry_partitions
from .structure_grouping import partitions as structure_partitions
from .natural_grouping import partitions as natural_partitions


DEFAULT_ALGORITHM = "graphistry"
_SEED = 42
GroupingProvider = Callable[[nx.Graph], list[set[str]]]


def _louvain(graph: nx.Graph) -> list[set[str]]:
    """Выделяет сообщества взвешенной модулярности с фиксированным seed."""
    return nx.community.louvain_communities(graph, weight="weight", resolution=1, seed=_SEED)


def _leiden(graph: nx.Graph) -> list[set[str]]:
    """Запускает встроенный Leiden NetworkX с той же метрикой, что у Louvain."""
    return nx.community.leiden_communities(
        graph, weight="weight", resolution=1, seed=_SEED, metric="modularity",
    )


def _greedy_modularity(graph: nx.Graph) -> list[set[str]]:
    """Объединяет сообщества жадно по приросту взвешенной модулярности."""
    return list(nx.community.greedy_modularity_communities(graph, weight="weight", resolution=1))


_PROVIDERS = {
    "natural": (
        natural_partitions, "Natural · ветви и группы",
        "Компактные связанные группы до 32 узлов; ветвление с местом для подписей, без движения при раскрытии пути.",
    ),
    "graphistry": (
        graphistry_partitions, "Graphistry (CPU · Louvain)",
        "Локальный PyGraphistry + igraph: взвешенный Louvain на CPU, без облака.",
    ),
    "graphistry-structure": (
        structure_partitions, "Graphistry · проекты и файлы",
        "Graphistry внутри проектов: файлы целиком, внешние ссылки присоединяются по связям.",
    ),
    "louvain": (_louvain, "Louvain", "Быстрая группировка по плотности взвешенных связей."),
    "leiden": (_leiden, "Leiden", "Группировка с дополнительным уточнением связности сообществ."),
    "greedy-modularity": (
        _greedy_modularity, "Greedy modularity", "Последовательное объединение по приросту модулярности.",
    ),
}


def list_grouping_algorithms() -> list[dict]:
    """Возвращает разрешённые провайдеры для выбора в интерфейсе."""
    return [
        {"id": identifier, "label": label, "description": description}
        for identifier, (_, label, description) in _PROVIDERS.items()
    ]


def get_provider(identifier: str) -> GroupingProvider:
    """Находит разрешённый провайдер; неизвестный идентификатор вызывает ValueError."""
    try:
        return _PROVIDERS[identifier][0]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Неизвестный алгоритм группировки: {identifier}") from exc
