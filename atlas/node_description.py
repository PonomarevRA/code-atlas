"""Детерминированное описание узла: факты графа и локального исходника без LLM."""

from pathlib import Path
from copy import deepcopy
from .analysis_diagnostics import diagnostic_summary

from .execution_path import ExecutionPathError, _bind_events, _edge_fact
from .execution_path_source import SourceAnalysis
from .project_scopes import assign_code_projects
from .node_comments import explanatory_comments
from .source_provenance import source_provenance
from .analysis_facts import description_analysis_facts

MAX_CALLS = 200
MAX_INCOMING_CALLERS = 8
MAX_INCOMING_CALL_SITES = 40
MAX_DESCRIPTION_FILES = 5


def _incoming_call_sites(analysis: SourceAnalysis, graph: dict, nodes: dict,
                         node_id: str, incoming: list[dict]) -> tuple[list[dict], bool]:
    """Читает ограниченное число caller-объявлений; аргументы привязывает только существующими calls-фактами."""
    caller_ids = []
    seen = set()
    for edge in incoming:
        caller = edge["source"]
        if edge["relation"] != "calls" or caller in seen:
            continue
        seen.add(caller)
        caller_ids.append(caller)
    truncated = len(caller_ids) > MAX_INCOMING_CALLERS
    caller_ids = caller_ids[:MAX_INCOMING_CALLERS]
    if not caller_ids:
        return [], False
    outgoing = {caller: [] for caller in caller_ids}
    for edge in graph.get("edges", []):
        if (edge.get("source") in outgoing and edge.get("target") in nodes
                and edge.get("relation") == "calls"):
            outgoing[edge["source"]].append(_edge_fact(edge))
    sites = []
    for caller in caller_ids:
        description = analysis.describe(nodes[caller])
        if description["sourceStatus"] != "parsed":
            truncated = True
            continue
        _bind_events(description, outgoing[caller], nodes)
        matched = False
        for event in description["events"]:
            if node_id not in event.get("targetNodeIds", []):
                continue
            matched = True
            if len(sites) >= MAX_INCOMING_CALL_SITES:
                truncated = True
                continue
            site = {**event, "callerNodeId": caller, "callerLabel": nodes[caller].get("label", caller)}
            if len(event["targetNodeIds"]) > 1:
                site["confidence"] = "candidate"
            sites.append(site)
        truncated |= not matched
    truncated |= any(analysis.truncation.values())
    return sites, truncated


def build_node_description(project_path: Path, graph: dict, node_id: str, snapshot_id: str) -> dict:
    """
    Описывает выбранный узел и ограниченные входящие callsites; источники и сокращения явные.

    Args:
        project_path: Корень checkout для ограниченного чтения обычных программных узлов.
        graph: Неизменяемый опубликованный снимок.
        node_id: Существующий raw-ID; диагностический узел не запускает чтение AST.
        snapshot_id: Ревизия результата и происхождения.
    Returns:
        Описание программных фактов либо отдельный analysis-diagnostic без вымышленных связей.
    Raises:
        ExecutionPathError: Узел отсутствует или необходимая грамматика недоступна.
    """
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    if node_id not in nodes:
        raise ExecutionPathError("Узел не найден в выбранном снимке.", 404)
    if nodes[node_id].get('kind') == 'analysis-error':
        node = nodes[node_id]
        return {**diagnostic_summary(graph), 'nodeId': node_id, 'snapshotId': snapshot_id,
                'label': node['label'], 'kind': node['kind'], 'file': node['file'],
                'sourceKind': 'analysis-diagnostic', 'sourceStatus': 'failed',
                'analysisIssue': deepcopy(node['analysisIssue']), 'factualSummary': node['description'],
                'readabilityRecommendations': {'status': 'not-applicable', 'items': []}}
    if nodes[node_id].get('htmlFacts'):
        node = nodes[node_id]
        return {'nodeId': node_id, 'label': node.get('label', node_id), 'kind': node.get('kind'),
            'file': node.get('file'), 'line': node.get('line'), 'snapshotId': snapshot_id,
            'sourceKind': 'snapshot-captured-source', 'sourceStatus': 'captured',
            'factualSummary': node.get('comment', ''), 'readabilityRecommendations': {
                'status': 'not-applicable', 'items': [], 'truncated': False},
            'warning': 'Захваченная статическая HTML-структура. Текущий файл и DOM могут отличаться.',
            **description_analysis_facts(node, graph)}
    if nodes[node_id].get("kind") in {"attribute", "attribute_scope"}:
        node = deepcopy(nodes[node_id])
        result = {"nodeId": node_id, "label": node.get("label", node_id), "kind": node["kind"],
                "snapshotId": snapshot_id, "sourceKind": "snapshot-captured-source",
                "sourceStatus": "captured", "capturedAttribute": node,
                "attributeFacts": node.get("attributeFacts", {}),
                "factualSummary": "Лексический атрибут из опубликованного снимка; значения и runtime-влияние не вычислены.",
                "warning": "Атрибут и его владелец зафиксированы при сборке карты; рабочий файл может измениться."}
        result["sourceProvenance"] = source_provenance(graph, node, result, snapshot_id)
        result.update(description_analysis_facts(node, graph))
        return result
    try:
        analysis = SourceAnalysis(project_path, snapshot_id, MAX_DESCRIPTION_FILES, 1024 * 1024, 160)
    except ImportError as error:
        raise ExecutionPathError("Локальная грамматика C# недоступна.", 503) from error
    result = analysis.describe(nodes[node_id])
    from .method_flow_chunks import method_flow_chunk_availability
    result['astExploration'] = method_flow_chunk_availability(nodes[node_id])
    result.update(description_analysis_facts(nodes[node_id], graph))
    from .infrastructure.index import infrastructure_index
    infrastructure = infrastructure_index(project_path, graph, snapshot_id)
    graph = {**graph, 'edges': [*graph.get('edges', []), *infrastructure.dispatches()]}
    result.update(infrastructure.node_metadata(nodes[node_id], result))
    result["sourceProvenance"] = source_provenance(graph, nodes[node_id], result, snapshot_id)
    source_kind = result.get('sourceKind', 'working-tree')
    result.update(explanatory_comments(result["documentation"], result.get("typeContext"),
                                      source_kind=source_kind, source_hash=result.get("sourceHash", ""),
                                      filename=result.get("file", ""), line=result.get("line")))
    captured_attributes = nodes[node_id].get("attributeFacts")
    if isinstance(captured_attributes, dict):
        # Ветви/документация читаются из рабочего дерева, но атрибуты принадлежат
        # снимку. Их provenance сохраняется внутри attributeFacts явно.
        result["attributeFacts"] = deepcopy(captured_attributes)
    incoming, outgoing = [], []
    for edge in graph.get("edges", []):
        if edge.get("relation") not in {"calls", "dispatches_to"}:
            continue
        if edge.get("source") not in nodes or edge.get("target") not in nodes:
            continue
        if edge["source"] != node_id and edge["target"] != node_id:
            continue
        fact = _edge_fact(edge)
        fact["sourceLabel"] = nodes[edge["source"]].get("label", edge["source"])
        fact["targetLabel"] = nodes[edge["target"]].get("label", edge["target"])
        if edge["source"] == node_id:
            outgoing.append(fact)
        if edge["target"] == node_id:
            incoming.append(fact)
    for edges in (incoming, outgoing):
        edges.sort(key=lambda edge: (edge["sourceLine"] or 0, edge["source"], edge["target"], edge["relation"]))
    _bind_events(result, outgoing, nodes)
    incoming_call_sites, incoming_sites_truncated = _incoming_call_sites(analysis, graph, nodes, node_id, incoming)
    projects = graph.get("codeProjects", [])
    project_id = nodes[node_id].get("codeProjectId")
    if not project_id or not projects:
        copies = [dict(node) for node in graph.get("nodes", [])]
        projects = assign_code_projects(copies)
        project_id = next(node["codeProjectId"] for node in copies if node["id"] == node_id)
    project = next((item for item in projects if item["id"] == project_id), None)
    counts = {"incoming": len(incoming), "outgoing": len(outgoing), "events": len(result["events"]),
              "logs": sum(event["kind"] == "log" for event in result["events"]),
              "spans": sum(event["kind"] == "span" for event in result["events"]),
              "branches": len(result["controlFlow"]["branches"])}
    summary = (f"Тип узла: {result['kind']}; состояние исходника: {result['sourceStatus']}. "
               f"Связей вызова/dispatch в снимке: входящих {counts['incoming']}, исходящих {counts['outgoing']}. "
               f"В выбранном объявлении найдено управляющих конструкций: {counts['branches']}, "
               f"лог-событий: {counts['logs']}, trace-событий/кандидатов: {counts['spans']}.")
    return {**result, "nodeId": node_id, "label": nodes[node_id].get("label", node_id),
            "snapshotId": snapshot_id, "sourceKind": source_kind, "project": project,
            "factualSummary": summary, "counts": counts,
            "calls": {"incoming": incoming[:MAX_CALLS], "outgoing": outgoing[:MAX_CALLS]},
            "incomingCallSites": incoming_call_sites, "incomingCallSitesTruncated": incoming_sites_truncated,
            "truncation": {**analysis.truncation, "incoming": len(incoming) > MAX_CALLS,
                           "outgoing": len(outgoing) > MAX_CALLS,
                           "documentation": result["documentation"]["truncated"],
                           "controlFlow": result["controlFlow"]["truncated"],
                           "parameterFacts": result["parameterFacts"]["truncated"],
                           "attributeFacts": bool(result["attributeFacts"].get("truncated")),
                           "arguments": any(event.get("argumentsTruncated", False) for event in result["events"]),
                           "incomingCallSites": incoming_sites_truncated,
                           "declaration": result["declarationTruncated"]},
            "limits": {"maxCallsPerDirection": MAX_CALLS, "maxFiles": MAX_DESCRIPTION_FILES, "maxBytes": 1024 * 1024,
                       "maxIncomingCallers": MAX_INCOMING_CALLERS, "maxIncomingCallSites": MAX_INCOMING_CALL_SITES,
                       "maxEventsPerMethod": 160},
            "warning": "Описание собрано из общего снимка и локального исходника, без LLM. Захваченные атрибуты имеют отдельный sourceKind в attributeFacts. Порядок и принадлежность ветвям лексические; достижимость, значения условий, выбор DI и исключения не вычисляются. Снимок графа и рабочий файл могут различаться. Для неизвестного типа или отсутствующего исходника полнота не заявляется."}
