"""Сообщества Graphistry внутри проектов с неделимыми файлами исходного кода."""

from collections import defaultdict
import hashlib
import posixpath

import networkx as nx

from .graphistry_provider import partitions_many


def _file_key(attributes: dict) -> str:
    """Нормализует сохранённый путь лексически, без доступа к рабочему дереву."""
    value = str(attributes.get("file") or attributes.get("source_file") or "")
    if not value.strip():
        return ""
    value = posixpath.normpath(value.replace("\\", "/"))
    return "" if value == "." else value


def _file_graph(graph: nx.Graph) -> tuple[nx.Graph, dict, dict]:
    """Сжимает каждый файл в один узел и сохраняет суммы межфайловых весов."""
    quotient = nx.Graph()
    members = defaultdict(set)
    membership = {}
    for node_id in sorted(graph.nodes):
        attributes = graph.nodes[node_id]
        filename = _file_key(attributes)
        if not filename:
            continue
        project = str(attributes.get("codeProjectId") or "__unassigned__")
        identifier = hashlib.sha256((project + "\0" + filename).encode()).hexdigest()
        membership[node_id] = identifier
        members[identifier].add(node_id)
        quotient.add_node(identifier, project=project)
    ordered_edges = []
    for source, target, attributes in graph.edges(data=True):
        left, right = sorted((source, target))
        ordered_edges.append((left, right, attributes.get("weight", 1)))
    for source, target, weight in sorted(ordered_edges):
        left, right = membership.get(source), membership.get(target)
        if left is None or right is None or left == right:
            continue
        if quotient.nodes[left]["project"] != quotient.nodes[right]["project"]:
            continue
        previous = quotient.get_edge_data(left, right, {}).get("weight", 0)
        quotient.add_edge(left, right, weight=previous + weight)
    return quotient, dict(members), membership


def _attach_references(graph: nx.Graph, groups: list[set[str]], core: dict) -> tuple[int, int]:
    """Присоединяет ссылки по прямым связям с файлами; это не владение проектом."""
    core_groups = {}
    for index, group in enumerate(groups):
        for node_id in group:
            core_groups[node_id] = index
    inferred = 0
    ambiguous = 0
    for node_id in sorted(set(graph.nodes) - set(core)):
        scores = defaultdict(float)
        for neighbor in sorted(graph.neighbors(node_id)):
            if neighbor in core_groups:
                scores[core_groups[neighbor]] += graph[node_id][neighbor].get("weight", 1)
        if not scores:
            continue
        # Индексы заданы стабильным порядком исходных групп, до присоединения ссылок.
        chosen = min(scores, key=lambda index: (-scores[index], index))
        groups[chosen].add(node_id)
        graph.nodes[node_id]["communityAssociation"] = "inferred"
        graph.nodes[node_id]["communityAssociationAmbiguous"] = len(scores) > 1
        inferred += 1
        ambiguous += len(scores) > 1
    return inferred, ambiguous


def partitions(graph: nx.Graph) -> list[set[str]]:
    """Группирует файловые области одним offline-процессом настоящего PyGraphistry.

    Межпроектные связи сохраняются в исходном графе, но не объединяют области.
    Узлы без пути не влияют на ядро: после расчёта они получают явно отмеченную
    ассоциацию по прямым соседям. Остаток группируется независимо тем же SDK.
    """
    quotient, members, core = _file_graph(graph)
    scopes = defaultdict(list)
    for identifier in sorted(quotient.nodes):
        scopes[quotient.nodes[identifier]["project"]].append(identifier)
    graphs = [quotient.subgraph(scopes[scope]).copy() for scope in sorted(scopes)]
    unattached = []
    for node_id in sorted(set(graph.nodes) - set(core)):
        if not any(neighbor in core for neighbor in graph.neighbors(node_id)):
            unattached.append(node_id)
    if unattached:
        graphs.append(graph.subgraph(unattached).copy())
    results = partitions_many(graphs) if graphs else []
    groups = []
    for partitions_in_scope in results[:len(scopes)]:
        for units in partitions_in_scope:
            group = set()
            for unit in units:
                group.update(members[unit])
            groups.append(group)
    groups.sort(key=lambda group: min(group))
    inferred, ambiguous = _attach_references(graph, groups, core)
    if unattached:
        groups.extend(results[-1])
    graph.graph["groupingStructure"] = {
        "fileUnits": len(members), "inferredNodes": inferred,
        "ambiguousNodes": ambiguous, "unattachedNodes": len(unattached),
    }
    return groups
