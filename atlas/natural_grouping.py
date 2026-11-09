"""Компактные связанные группы: плотные области графа разбиваются на небольшие ветви."""

import heapq

import networkx as nx

MAX_GROUP_SIZE = 32


def _branches(graph: nx.Graph, members: set[str]) -> list[set[str]]:
    """Растит группы по сильным связям внутри сообщества, не переходя лимит 32 узлов."""
    remaining = set(members)
    strengths = dict(graph.subgraph(members).degree(weight="weight"))
    roots = sorted(members, key=lambda node: (-strengths[node], node))
    result = []
    for root in roots:
        if root not in remaining:
            continue
        branch = set()
        frontier = [(0, root)]
        while frontier and len(branch) < MAX_GROUP_SIZE:
            _, node = heapq.heappop(frontier)
            if node not in remaining:
                continue
            remaining.remove(node)
            branch.add(node)
            for neighbor, attributes in graph[node].items():
                if neighbor in remaining:
                    heapq.heappush(frontier, (-attributes.get("weight", 1), neighbor))
        result.append(branch)
    return result


def partitions(graph: nx.Graph) -> list[set[str]]:
    """Возвращает связные непустые группы с полным покрытием и стабильным порядком ID.

    Natural — эвристика читаемости, а не модель исполнения или биологический закон.
    Сначала выделяем плотные области, затем растим ограниченные группы внутри них.
    Отсортированная копия делает seed независимым от порядка исходного графа.
    """
    if not graph:
        return []
    ordered = nx.Graph()
    ordered.add_nodes_from(sorted(graph.nodes))
    for source, target, attributes in sorted(
        graph.edges(data=True), key=lambda edge: tuple(sorted(edge[:2])),
    ):
        ordered.add_edge(*sorted((source, target)), **attributes)
    if not ordered.number_of_edges():
        return [{node} for node in ordered]
    communities = nx.community.louvain_communities(
        ordered, weight="weight", resolution=1.4, seed=42,
    )
    result = []
    for community in sorted(communities, key=lambda members: min(members)):
        result.extend(_branches(ordered.subgraph(community), set(community)))
    return sorted(result, key=lambda members: min(members))
