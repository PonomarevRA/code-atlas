"""Детерминированные сообщества связей и координаты для локальной карты кода."""

from collections import Counter
import hashlib
import math
from pathlib import PurePosixPath

import networkx as nx

from .grouping_providers import DEFAULT_ALGORITHM, get_provider
from .project_scopes import assign_code_projects


GROUPING_VERSION = "providers-v3"
_SEED = 42
_STRUCTURAL_RELATIONS = {"contains", "defines", "method", "case_of"}


def _weighted_graph(nodes: list[dict], edges: list[dict]) -> nx.Graph:
    """Создаёт граф группировки; исходные направления и факты остаются в JSON."""
    graph = nx.Graph()
    by_id = {node["id"]: node for node in nodes}
    graph.add_nodes_from(sorted(by_id))
    weights = Counter()
    for edge in edges:
        source, target = edge["source"], edge["target"]
        if source not in by_id or target not in by_id or source == target:
            continue
        relation = edge.get("relation", "")
        # Целые веса делают суммирование независимым от порядка входных связей.
        weight = 10
        if relation in _STRUCTURAL_RELATIONS:
            weight = 20
        elif relation in {"references", "imports"}:
            weight = 6
        pair = tuple(sorted((source, target)))
        weights[pair] += weight
    for (source, target), weight in sorted(weights.items()):
        graph.add_edge(source, target, weight=weight / 10)

    # Общие библиотеки наподобие System и xUnit не должны объединять весь код.
    # Ослабляем только внешние узлы с >20 соседями; реальные внутренние центры
    # сохраняют влияние, чтобы не скрыть архитектурную связанность приложения.
    hub_factors = {}
    for node_id, degree in graph.degree:
        if by_id[node_id].get("kind") == "external" and degree > 20:
            hub_factors[node_id] = math.sqrt(20 / degree)
    for source, target, data in graph.edges(data=True):
        data["weight"] *= min(hub_factors.get(source, 1), hub_factors.get(target, 1))
    return graph


def _community_id(members: list[str]) -> str:
    """Выводит стабильный идентификатор из состава, независимо от порядка входа."""
    digest = hashlib.sha256()
    for member in members:
        encoded = member.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return "c-" + digest.hexdigest()[:16]


def _directory(node: dict) -> str:
    """Возвращает каталог определения; внешние ссылки не получают выдуманный путь."""
    filename = str(node.get("file") or node.get("source_file") or "")
    parent = str(PurePosixPath(filename.replace("\\", "/")).parent)
    return "" if parent == "." else parent


def _label(members: list[str], by_id: dict[str, dict], graph: nx.Graph) -> str:
    """Называет группу по преобладающему каталогу либо её значимому символу."""
    directories = Counter()
    for member in members:
        directory = _directory(by_id[member])
        if directory:
            directories[directory] += 1
    if directories:
        directory = min(directories, key=lambda value: (-directories[value], value))
        return "/".join(PurePosixPath(directory).parts[-2:])
    candidates = [member for member in members if by_id[member].get("kind") != "external"]
    if not candidates:
        candidates = members
    chosen = min(candidates, key=lambda member: (-graph.degree[member], member))
    return str(by_id[chosen].get("label") or chosen)


def _radial_positions(identifiers: list[str]) -> dict[str, tuple[float, float]]:
    """Размещает большие наборы за O(n), без квадратичного расчёта отталкивания."""
    count = len(identifiers)
    if count == 1:
        return {identifiers[0]: (0.0, 0.0)}
    positions = {}
    golden_angle = math.pi * (3 - math.sqrt(5))
    for index, node_id in enumerate(identifiers):
        radius = math.sqrt((index + 0.5) / max(1, count))
        angle = index * golden_angle
        positions[node_id] = (radius * math.cos(angle), radius * math.sin(angle))
    return positions


def _positions(graph: nx.Graph, ordered_ids: list[str], spring_limit: int) -> dict:
    """Использует пружинную раскладку с seed для малых групп, линейную для больших."""
    if not ordered_ids:
        return {}
    if len(ordered_ids) == 1:
        return {ordered_ids[0]: (0.0, 0.0)}
    if len(ordered_ids) > spring_limit or graph.number_of_edges() == 0:
        return _radial_positions(ordered_ids)
    # Представление subgraph может обходить маленький набор через set. Явный
    # порядок узлов и рёбер исключает зависимость раскладки от PYTHONHASHSEED.
    ordered_graph = nx.Graph()
    ordered_graph.add_nodes_from(ordered_ids)
    ordered_edges = []
    for source, target, data in graph.edges(data=True):
        left, right = sorted((source, target))
        ordered_edges.append((left, right, data.get("weight", 1)))
    for source, target, weight in sorted(ordered_edges):
        ordered_graph.add_edge(source, target, weight=weight)
    initial = _radial_positions(ordered_ids)
    positions = nx.spring_layout(
        ordered_graph, pos=initial, seed=_SEED, iterations=30, weight="weight", scale=1.0,
    )
    result = {}
    for node_id, position in positions.items():
        result[node_id] = (float(position[0]), float(position[1]))
    return result


def _summaries(communities: list[dict], edges: list[dict], membership: dict[str, str]) -> list[dict]:
    """Считает исходные связи внутри и между группами, не теряя повторные факты."""
    by_id = {community["id"]: community for community in communities}
    cross_edges = Counter()
    for edge in edges:
        source = membership.get(edge["source"])
        target = membership.get(edge["target"])
        if source is None or target is None:
            continue
        if source == target:
            by_id[source]["internalEdges"] += 1
        else:
            by_id[source]["externalEdges"] += 1
            by_id[target]["externalEdges"] += 1
            cross_edges[tuple(sorted((source, target)))] += 1
    return [
        {"source": source, "target": target, "weight": weight}
        for (source, target), weight in sorted(cross_edges.items())
    ]


def enrich_graph(graph: dict, algorithm: str = DEFAULT_ALGORITHM, *, provider_override=None) -> dict:
    """Добавляет выбранные сообщества, подпроекты и координаты без изменения входа.

    node.x/y лежат в локальной системе группы [-1,1], community.x/y — в системе
    обзора [-1,1]. Связи, идентификаторы и исходные атрибуты сохраняются.
    Структурный режим удерживает файл целиком; прочие режимы выделяют изоляты.
    """
    provider = provider_override or get_provider(algorithm)
    nodes = [dict(node) for node in graph.get("nodes", [])]
    for node in nodes:
        node.pop("communityAssociation", None)
        node.pop("communityAssociationAmbiguous", None)
    code_projects = assign_code_projects(nodes)
    edges = graph.get("edges", [])
    weighted = _weighted_graph(nodes, edges)
    by_id = {node["id"]: node for node in nodes}
    if algorithm == "graphistry-structure":
        for node in nodes:
            weighted.nodes[node["id"]].update(node)
    elif provider_override:
        for node in nodes:
            weighted.nodes[node['id']].update({key:node.get(key,'unknown') for key in ('label','kind','file','codeProjectId')})
            weighted.nodes[node['id']]['language'] = (node.get('memberFacts') or {}).get('language','unknown')
            weighted.nodes[node['id']]['health.level'] = (node.get('health') or {}).get('level','unknown')
    if weighted.number_of_edges() or algorithm == "graphistry-structure" or provider_override:
        partitions = provider(weighted)
    else:
        partitions = [{node_id} for node_id in weighted.nodes]
    if algorithm == "graphistry-structure":
        for node in nodes:
            attributes = weighted.nodes[node["id"]]
            for key in ("communityAssociation", "communityAssociationAmbiguous"):
                if key in attributes:
                    node[key] = attributes[key]
    groups = sorted((sorted(partition) for partition in partitions), key=lambda members: members[0])
    communities = []
    membership = {}
    label_counts = Counter()
    for members in groups:
        community_id = _community_id(members)
        label = _label(members, by_id, weighted)
        label_counts[label] += 1
        if label_counts[label] > 1:
            label = f"{label} · {label_counts[label]}"
        communities.append({
            "id": community_id, "label": label, "nodeCount": len(members),
            "internalEdges": 0, "externalEdges": 0,
        })
        local_order = sorted(members, key=lambda member: (_directory(by_id[member]), member))
        positions = _positions(weighted.subgraph(members), local_order, spring_limit=120)
        for member in members:
            membership[member] = community_id
            by_id[member]["community"] = community_id
            by_id[member]["x"], by_id[member]["y"] = positions[member]
    community_edges = _summaries(communities, edges, membership)
    overview = nx.Graph()
    overview.add_nodes_from(community["id"] for community in communities)
    for edge in community_edges:
        overview.add_edge(edge["source"], edge["target"], weight=edge["weight"])
    positions = _positions(overview, sorted(overview.nodes), spring_limit=400)
    for community in communities:
        community["x"], community["y"] = positions[community["id"]]
    metadata = dict(graph.get("metadata", {}))
    metadata.pop("groupingStructure", None)
    if algorithm == "graphistry-structure":
        metadata["groupingStructure"] = weighted.graph["groupingStructure"]
    metadata.update({
        "groupingVersion": GROUPING_VERSION,
        "groupingProvider": algorithm,
        "groupingAlgorithm": algorithm,
    })
    return {**graph, "nodes": nodes, "communities": communities,
            "communityEdges": community_edges, "codeProjects": code_projects, "metadata": metadata}
