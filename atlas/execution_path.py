"""Ограниченный статический путь calls/dispatches_to поверх неизменяемого снимка."""

from collections import defaultdict, deque
from pathlib import Path
import re

import networkx as nx

from .execution_path_source import SourceAnalysis
from .interface_dispatch import graph_dispatch_edges
from .source_provenance import source_provenance
from .execution_path_limits import MAX_PATH_NODES


MAX_FILES = 240
MAX_BYTES = 24 * 1024 * 1024
MAX_EVENTS_PER_METHOD = 160
MAX_NAVIGATION_TARGETS = 50


class ExecutionPathError(RuntimeError):
    """Ожидаемая ошибка выбора корня или ограничения запроса."""

    def __init__(self, message: str, status: int = 400):
        """Сохраняет безопасное сообщение и соответствующий HTTP-статус."""
        super().__init__(message)
        self.status = status


def _line(edge: dict) -> int | None:
    """Извлекает только положительный номер строки из доказательства Graphify."""
    value = edge.get("source_location")
    match = re.match(r"L(\d+)", str(value or ""))
    if match:
        return int(match[1]) or None
    value = edge.get("line")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _method_name(label: str) -> str:
    """Сводит подпись метода к имени без класса и списка параметров."""
    text = str(label or "").strip().split("(", 1)[0]
    return text.rsplit(".", 1)[-1]


def _adjacency(graph: dict, root_id: str, nodes: dict) -> dict:
    """Допускает вызовы, dispatch и найденные реализации интерфейса."""
    adjacency = defaultdict(list)
    for edge in [*graph.get("edges", []), *graph_dispatch_edges(graph, nodes)]:
        source, target = edge.get("source"), edge.get("target")
        relation = edge.get("relation")
        if source not in nodes or target not in nodes:
            continue
        structural = relation in {"method", "field", "inherits", "inherits_from", "extends", "implements"} and (
            source == root_id or nodes[source].get("_callable_class") or nodes[source].get("typeFacts")
            or nodes[source].get("kind") in {"class", "interface", "struct", "record"})
        if relation == 'field' and not (nodes[target].get('staticData') or nodes[target].get('fieldFacts')):
            structural = False
        data_edge = relation in {"reads", "writes", "initializes_from"} and ((nodes[target].get("staticData") or nodes[target].get("fieldFacts")) or relation == "initializes_from" and nodes[source].get("staticData"))
        if relation not in {"calls", "dispatches_to"} and not structural and not data_edge:
            continue
        adjacency[source].append(edge)
    for edges in adjacency.values():
        edges.sort(key=lambda edge: (_line(edge) or 0, edge["relation"], edge["target"], str(edge.get("confidence", ""))))
    return adjacency


def _reachable(root_id: str, adjacency: dict) -> int:
    """Считает полную достижимость сохранённого call-графа, не читая исходные файлы."""
    seen, pending = {root_id}, [root_id]
    while pending:
        node_id = pending.pop()
        for edge in adjacency.get(node_id, []):
            target = edge["target"]
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return len(seen)


def _edge_fact(edge: dict) -> dict:
    """Сохраняет происхождение графового факта и явно маркирует предполагаемый dispatch."""
    original = str(edge.get("confidence") or "UNKNOWN")
    candidate = edge["relation"] in {"dispatches_to", "method"} or original.upper() == "INFERRED"
    evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else {}
    fact = {"source": edge["source"], "target": edge["target"], "relation": edge["relation"],
            "confidence": "candidate" if candidate else "graph", "graphConfidence": original,
            "sourceLine": _line(edge) if edge["relation"] == "calls" or evidence.get("kind") == "method-group-binding" else None,
            "lineKind": edge.get("lineKind") if edge.get("lineKind") in {"snapshot", "working-tree", "working-tree-ast", "snapshot-captured-ast"} else "snapshot", "order": None, "branchIds": [], "branchContextKnown": False}
    if edge["relation"] in {"reads", "writes", "initializes_from", "field", "inherits", "inherits_from", "extends", "implements"}:
        fact["sourceLine"] = _line(edge)
    if evidence:
        fields = {"kind", "receiverType", "signature", "interfaceType", "implementationType", "sourceKind", "file", "parameter", "bindingFile", "declaringType", "implementationProjectId", "callKind", "expression", "callee", "requestType"}
        fact["evidence"] = {key: str(value)[:700] for key, value in evidence.items() if key in fields and isinstance(value, str)}
        for key in {"line", "bindingLine"}:
            if isinstance(evidence.get(key), int) and not isinstance(evidence[key], bool) and evidence[key] > 0: fact["evidence"][key] = evidence[key]
        for key in {"runtimeSelectionKnown", "signatureKnown", "ambiguous"}:
            if isinstance(evidence.get(key), bool): fact["evidence"][key] = evidence[key]
    if isinstance(edge.get("source_file"), str): fact["sourceFile"] = edge["source_file"][:700]
    return fact


def _bind_events(step: dict, outgoing: list[dict], nodes: dict) -> None:
    """Связывает AST-вызовы только с исходящими calls; имя без точной строки остаётся гипотезой."""
    by_name = defaultdict(list)
    for edge in outgoing:
        if edge["relation"] == "calls":
            name = str(nodes[edge["target"]].get("label", "")).strip(".").split("(", 1)[0]
            if edge.get("evidence", {}).get("callKind") == "constructor-initializer":
                expression = edge["evidence"].get("expression", "").lstrip(": ")
                name = "this" if expression.startswith("this") else "base"
            by_name[name].append(edge)
        elif edge.get("evidence", {}).get("kind") == "method-group-binding":
            parameter = edge["evidence"].get("parameter")
            if isinstance(parameter, str): by_name[parameter].append(edge)
    contexts = defaultdict(list)
    occurrence_counts = defaultdict(int)
    for event in step["events"]:
        if event["kind"] in {"call", "log", "span"}:
            occurrence_counts[(event.get("callee"), event["line"])] += 1
    for order, event in enumerate(step["events"], 1):
        event["order"] = order
        if event["kind"] not in {"call", "log", "span"}:
            continue
        candidates = list(by_name.get(event.get("callee"), []))
        candidates.extend(edge for edge in outgoing if edge.get('evidence', {}).get('kind') == 'mediator-dispatch'
                          and edge.get('evidence', {}).get('callee') == event.get('callee')
                          and edge.get('sourceLine') == event.get('line') and edge not in candidates)
        exact = [edge for edge in candidates if edge["sourceLine"] == event["line"]]
        matched = exact or [edge for edge in candidates if edge.get("evidence", {}).get("kind") != "method-group-binding"]
        # A typed receiver at this source line is stronger than Graphify's same-name guess,
        # which can accidentally turn an interface call into recursion on its caller.
        # Line-only evidence cannot distinguish two same-name calls on that line.
        typed = [edge for edge in exact if edge.get("evidence", {}).get("kind") == "typed-interface-call"]
        if typed and occurrence_counts[(event.get("callee"), event["line"])] == 1:
            matched = typed
        event["targetNodeIds"] = sorted({edge["target"] for edge in matched})
        if matched:
            uncertain = event["confidence"] == "candidate" or not exact
            uncertain |= any(edge["confidence"] == "candidate" for edge in matched)
            event["confidence"] = "candidate" if uncertain else "graph"
            for edge in matched:
                if exact:
                    edge.update(sourceLine=event["line"], lineKind="working-tree", order=order)
                    contexts[id(edge)].append(list(event.get("branchIds", [])))
        elif event["kind"] == "call":
            event["confidence"] = "unresolved"
    for edge in outgoing:
        matched_contexts = contexts.get(id(edge), [])
        if matched_contexts and all(context == matched_contexts[0] for context in matched_contexts):
            edge["branchIds"] = matched_contexts[0]
            edge["branchContextKnown"] = True
    unresolved_calls = [event for event in step["events"] if event["kind"] == "call" and not event["targetNodeIds"]]
    if unresolved_calls:
        step["unresolved"].append(f"Без продолжения в выбранном подграфе: {len(unresolved_calls)} AST-вызовов. Причина может быть в лимите, внешнем/динамическом вызове или неполном разрешении Graphify.")


def _cycles(steps: list[dict], edges: list[dict]) -> list[dict]:
    """Отмечает реальные циклы выбранного подграфа, не путая общий потомок с рекурсией."""
    graph = nx.DiGraph()
    graph.add_nodes_from(step["nodeId"] for step in steps)
    graph.add_edges_from((edge["source"], edge["target"]) for edge in edges)
    owner, cyclic = {}, set()
    for index, component in enumerate(nx.strongly_connected_components(graph)):
        for node_id in component:
            owner[node_id] = index
        if len(component) > 1 or any(graph.has_edge(node_id, node_id) for node_id in component):
            cyclic.add(index)
    pairs = set()
    for edge in edges:
        if owner[edge["source"]] == owner[edge["target"]] and owner[edge["source"]] in cyclic:
            pairs.add((edge["source"], edge["target"]))
    return [{"source": source, "target": target} for source, target in sorted(pairs)]


def build_execution_path(project_path: Path, graph: dict, node_id: str, snapshot_id: str,
                         max_depth: int = 8, max_nodes: int = MAX_PATH_NODES, *,
                         snapshot_calls_only: bool = False) -> dict:
    """Обходит возможные вызовы; режим обзора ограничивает связи опубликованным графом."""
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 30:
        raise ExecutionPathError("Глубина должна быть от 1 до 30.")
    if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or not 1 <= max_nodes <= MAX_PATH_NODES:
        raise ExecutionPathError(f"Количество узлов должно быть от 1 до {MAX_PATH_NODES}.")
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    if node_id not in nodes:
        raise ExecutionPathError("Узел не найден в выбранном снимке.", 404)
    try:
        source = SourceAnalysis(project_path, snapshot_id, MAX_FILES, MAX_BYTES, MAX_EVENTS_PER_METHOD)
    except ImportError as error:
        raise ExecutionPathError("Локальная грамматика C# недоступна; переустановите offline-зависимости.", 503) from error
    infrastructure = None
    path_graph = graph
    if snapshot_calls_only:
        edges = [edge for edge in graph.get('edges', [])
                 if (edge.get('relation') or edge.get('type')) in {'calls', 'dispatches_to'}]
        path_graph = {**graph, 'edges': edges}
    else:
        from .infrastructure.index import infrastructure_index
        infrastructure = infrastructure_index(project_path, graph, snapshot_id)
        path_graph = {**graph, 'edges': [*graph.get('edges', []), *infrastructure.dispatches()]}
    adjacency = _adjacency(path_graph, node_id, nodes)
    root_description = source.describe(nodes[node_id])
    if root_description["kind"] != "class":
        adjacency[node_id] = [edge for edge in adjacency.get(node_id, []) if edge["relation"] != "method"]
    total_reachable = _reachable(node_id, adjacency)
    discovered = {node_id: 0}
    pending = deque([node_id])
    steps, edges = [], []
    truncation = {"nodes": False, "depth": False, "files": False, "bytes": False, "events": False}
    if graph.get("metadata", {}).get("sourceStructure", {}).get("truncated"):
        truncation['staticData'] = True
    while pending:
        current = pending.popleft()
        depth = discovered[current]
        description = root_description if current == node_id else source.describe(nodes[current])
        description["sourceProvenance"] = source_provenance(graph, nodes[current], description, snapshot_id)
        step = {"nodeId": current, "label": nodes[current].get("label", current), "depth": depth, **description}
        if infrastructure is not None:
            step.update(infrastructure.node_metadata(nodes[current], description))
        local_edges = []
        for edge in adjacency.get(current, []):
            target = edge["target"]
            if target not in discovered:
                if depth >= max_depth:
                    truncation["depth"] = True
                    continue
                if len(discovered) >= max_nodes:
                    truncation["nodes"] = True
                    continue
                discovered[target] = depth + 1
                pending.append(target)
            fact = _edge_fact(edge)
            if edge['relation'] == 'method':
                fact['sourceLine'] = nodes[target].get('line')
            edges.append(fact)
            local_edges.append(fact)
        _bind_events(step, local_edges, nodes)
        # Binding to a snapshot interface remains useful when its body fell outside this path budget.
        shadow = {'events': [dict(event) for event in step['events']], 'unresolved': []}
        _bind_events(shadow, [_edge_fact(edge) for edge in adjacency.get(current, [])], nodes)
        for event, full_event in zip(step['events'], shadow['events']):
            # A body omitted by the path budget still has a graph-confirmed
            # address. Navigation references do not add visited nodes or edges.
            full_targets = full_event.get('targetNodeIds', [])
            if set(full_targets) - set(event.get('targetNodeIds', [])):
                event['navigationTargetIds'] = full_targets[:MAX_NAVIGATION_TARGETS]
                event['navigationTargetCount'] = len(full_targets)
                event['navigationTargetsTruncated'] = len(full_targets) > MAX_NAVIGATION_TARGETS
                event['navigationConfidence'] = full_event['confidence']
            event['implementationTargetIds'] = [target for target in full_event.get('targetNodeIds', [])
                                                if any(e.get('relation') == 'dispatches_to' for e in adjacency.get(target, []))]
        if any(edge["relation"] == "dispatches_to" for edge in local_edges):
            step["unresolved"].append("Реализация интерфейса — кандидат Graphify; фактический выбор DI/runtime не установлен.")
        if current == node_id and step["kind"] == "class":
            step["unresolved"].append("Методы выбранного класса показаны как альтернативные начала; порядок их исполнения не установлен.")
        steps.append(step)
    truncation.update(source.truncation)
    from .implementation_catalog import implementation_catalog
    catalog_ids = {target for step in steps for event in step['events'] for target in event.get('implementationTargetIds', [])}
    catalog_ids.update(step['nodeId'] for step in steps if any(e.get('relation') == 'dispatches_to' for e in adjacency.get(step['nodeId'], [])))
    return {
        "infrastructureCoverage": infrastructure.coverage if infrastructure is not None else {"status": "not-requested"},
        "snapshotId": snapshot_id, "rootNodeId": node_id, "sourceKind": "working-tree", "orderKind": "source-order",
        "rootKind": root_description["kind"],
        "sourceProvenance": root_description["sourceProvenance"],
        "steps": steps, "edges": edges, "cycles": _cycles(steps, edges), "truncation": truncation,
        "implementationCatalog": implementation_catalog(nodes, {key: adjacency[key] for key in sorted(catalog_ids)}, adjacency),
        "totalReachableNodes": total_reachable, "returnedNodes": len(steps),
        "limits": {"maxDepth": max_depth, "maxNodes": max_nodes, "maxFiles": MAX_FILES,
                   "maxBytes": MAX_BYTES, "maxEventsPerMethod": MAX_EVENTS_PER_METHOD},
        "warning": "Статические вызовы, объявления методов, наследование и чтение данных показаны разными связями. Это не runtime-трасса: методы класса — альтернативные начала; фабрика поля описывает инициализацию, не вызов при каждом чтении. Условия, циклы и значения не вычисляются. Исходники могли измениться после снимка. references не считаются вызовами; внешние и неразрешённые переходы могут отсутствовать.",
    }
