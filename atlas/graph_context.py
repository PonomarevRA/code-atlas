"""Ограниченное чтение фактов опубликованного графа без исходников, I/O и новых связей."""

from copy import deepcopy
from .analysis_diagnostics import diagnostic_summary
import json
from hashlib import sha256
from .continuation import decode_reference, encode_reference
from .node_comments import COMMENT_FIELDS
from .member_preview import member_preview
from .analysis_facts import analysis_context, analysis_preview

MAX_RESPONSE_BYTES = 64_000
_NODE_FIELDS = ("id", "label", "kind", "file", "line", "ownerNodeId", "declarationNodeId",
                "scope", "target", "text", "semanticFacts", "analysisIssue", "sourceKind") + COMMENT_FIELDS
_IDENTITY_FIELDS = {"id", "source", "target", "ownerNodeId", "declarationNodeId",
                    "attributeNodeIds", "nodeId", "snapshotId", "graph_node_id", "symbol_id"}


class GraphContextError(RuntimeError):
    """Контролируемая ошибка выбора узла или слишком большого обязательного идентификатора."""

    def __init__(self, message: str, status: int = 400):
        """Сохраняет HTTP-статус для API; неизвестный raw-ID даёт 404."""
        super().__init__(message)
        self.status = status


def _size(value: dict) -> int:
    """Считает реальный UTF-8 JSON с обычными пробелами, включая кириллицу и escaping."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _bounded_value(value, *, chars: int = 600, items: int = 200, depth: int = 0,
                   identity: bool = False):
    """Копирует JSON с пределами вложенности/коллекций; raw-ID не обрезаются посередине.

    Возвращает пару (копия, было усечение). Вызывающий выставляет видимый truncated.
    Это ограничение ответа, а не новый анализ или изменение сохранённых фактов.
    """
    if isinstance(value, str):
        return (value, False) if identity else (value[:chars], len(value) > chars)
    if not isinstance(value, (dict, list)):
        return value, False
    if depth >= 12:
        return ({} if isinstance(value, dict) else []), bool(value)
    shortened = len(value) > items
    if isinstance(value, list):
        result = []
        for entry in value[:items]:
            copied, limited = _bounded_value(entry, chars=chars, items=items, depth=depth + 1, identity=identity)
            result.append(copied)
            shortened |= limited
        return result, shortened
    result = {}
    for index, (key, entry) in enumerate(value.items()):
        if index >= items:
            break
        copied, limited = _bounded_value(entry, chars=chars, items=items, depth=depth + 1,
                                         identity=key in _IDENTITY_FIELDS or key.endswith("NodeId"))
        result[key] = copied
        shortened |= limited
        if limited and key == "attributeNodeIds":
            result["attributeNodeIdsTruncated"] = True
    if shortened:
        result["truncated"] = True
    return result, shortened


def _nodes(graph: dict, node_id: str) -> dict:
    """Индексирует только сохранённые узлы и проверяет существование выбранного ID."""
    if not isinstance(node_id, str):
        raise GraphContextError("nodeId должен быть строкой.")
    by_id = {node["id"]: node for node in graph.get("nodes", [])}
    if node_id not in by_id:
        raise GraphContextError("Узел отсутствует в выбранном снимке графа.", 404)
    return by_id


def _single_node(graph: dict, node_id: str) -> dict:
    """Find one captured node without allocating a full-map index; duplicate IDs keep the last value."""
    if not isinstance(node_id, str):
        raise GraphContextError("nodeId должен быть строкой.")
    for node in reversed(graph.get("nodes", [])):
        if node["id"] == node_id:
            return node
    raise GraphContextError("Узел отсутствует в выбранном снимке графа.", 404)


def _compact_node(node: dict) -> dict:
    """Для соседства оставляет местоположение и владельцев, без больших attributeFacts."""
    facts = {key: node[key] for key in _NODE_FIELDS if key in node}
    facts.update(member_preview(node))
    result, shortened = _bounded_value(facts)
    if shortened:
        result["truncated"] = True
    return result


def _trim_attributes(result: dict) -> None:
    """Сначала сокращает большие списки атрибутов, сохраняя metadata каждой scope-группы."""
    facts = result["node"].get("attributeFacts")
    if not isinstance(facts, dict):
        return
    facts["truncated"] = True
    groups = facts.get("groups", [])
    if not isinstance(groups, list):
        return
    while _size(result) > MAX_RESPONSE_BYTES:
        largest = None
        largest_size = 0
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("attributes"), list):
                continue
            if not group["attributes"]:
                continue
            size = _size({"attributes": group["attributes"]})
            if size > largest_size:
                largest, largest_size = group, size
        if largest is None:
            break
        # Убираем список целиком: число обходов ограничено количеством scope-групп.
        largest["attributes"] = []
        largest["truncated"] = True


def read_graph_node(graph: dict, node_id: str) -> dict:
    """
    Возвращает полный захваченный узел и attributeGraph; ответ не превышает 64_000 UTF-8 байт.

    При превышении бюджета сначала сокращается текст, затем списки attributeFacts.
    Scope-метаданные и пригодные attributeNodeIds сохраняются; пропуски отмечены.
    Неизвестный ID: GraphContextError(404). Невмещаемые обязательные ID: 413.

    Args:
        graph: Закреплённый граф, который не изменяется.
        node_id: Существующий полный raw-ID.
    Returns:
        Ограниченная карточка с сохранёнными счётчиками полноты.
    """
    node = _single_node(graph, node_id)
    metadata = graph.get("metadata", {})
    result = {**diagnostic_summary(graph), "snapshotId": metadata.get("snapshotId"), "node": deepcopy(node),
              "analysisContext": analysis_context(graph),
              "attributeGraph": deepcopy(metadata.get("attributeGraph", {})),
              "memberGraph": deepcopy(metadata.get("memberGraph", {})), "truncated": False}
    result["analysisFacts"] = analysis_preview(node)
    if _size(result) <= MAX_RESPONSE_BYTES:
        return result
    result, _ = _bounded_value(result, chars=600)
    result["truncated"] = True
    result["node"]["truncated"] = True
    _trim_attributes(result)
    if _size(result) <= MAX_RESPONSE_BYTES:
        return result
    # Второй бюджет уменьшает длинные описания, но не режет raw-ID.
    result, _ = _bounded_value(result, chars=160, items=200)
    protected = set(_NODE_FIELDS) | {"attributeFacts", "attributeNodeIds", "truncated"}
    node = result["node"]
    omitted = []
    for key in sorted(set(node) - protected, key=lambda key: _size({key: node[key]}), reverse=True):
        if _size(result) <= MAX_RESPONSE_BYTES:
            break
        del node[key]
        omitted.append(key)
    if omitted:
        node["omittedFields"] = omitted
    facts = node.get("attributeFacts")
    groups = facts.get("groups") if isinstance(facts, dict) else None
    while isinstance(groups, list) and groups and _size(result) > MAX_RESPONSE_BYTES:
        groups.pop()
        facts["truncated"] = True
    ids = node.get("attributeNodeIds")
    while isinstance(ids, list) and ids and _size(result) > MAX_RESPONSE_BYTES:
        ids.pop()
        node["attributeNodeIdsTruncated"] = True
    if _size(result) > MAX_RESPONSE_BYTES:
        raise GraphContextError("Обязательные факты узла превышают бюджет ответа.", 413)
    return result


def read_graph_neighbors(graph: dict, node_id: str, direction: str = "both", limit: int = 40,
                         cursor: str | None = None) -> dict:
    """
    Читает одно непосредственное соседство снимка, сохраняя направления и типы связей.

    direction: incoming/outgoing/both; limit: 1..120 рёбер (не глубина обхода).
    total — все подходящие рёбра с существующими концами, включая повторы связей.
    Самопетля считается один раз. Узлы — выбранный и концы возвращённых рёбер.
    Список может быть короче limit из-за 64_000-байтового UTF-8 бюджета.
    nextRef продолжает с первого неотданного ребра, включая повторные отношения.
    Курсор другой ревизии/области даёт 409, повреждённый — 400.

    Args:
        graph: Закреплённый граф, который не изменяется.
        node_id: Существующий raw-ID начального узла.
        direction: incoming, outgoing или both.
        limit: До 120 непосредственных рёбер, не глубина обхода.
        cursor: Необязательная ссылка продолжения той же области и ревизии.
    Returns:
        Страница соседей с явной полнотой; неверные ссылки отвергаются.
    """
    if not isinstance(direction, str) or direction not in {"incoming", "outgoing", "both"}:
        raise GraphContextError("direction должен быть incoming, outgoing или both.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 120:
        raise GraphContextError("limit должен быть целым числом от 1 до 120.")
    by_id = _nodes(graph, node_id)
    matching = []
    for edge in graph.get("edges", []):
        if edge.get("source") not in by_id or edge.get("target") not in by_id:
            continue
        incoming = edge["target"] == node_id and direction in {"incoming", "both"}
        outgoing = edge["source"] == node_id and direction in {"outgoing", "both"}
        if incoming or outgoing:
            matching.append(edge)
    fingerprint = sha256()
    for edge in matching:
        fingerprint.update(json.dumps(edge, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode())
        fingerprint.update(b'\n')
    scope = {'version': 'atlas-neighbors-v2', 'snapshotId': graph.get('metadata', {}).get('snapshotId'),
             'nodeId': node_id, 'direction': direction, 'edgeRevision': fingerprint.hexdigest()}
    offset = 0
    if cursor is not None:
        try:
            reference = decode_reference(cursor)
            if set(reference) != {'scope', 'offset'} or type(reference['offset']) is not int:
                raise ValueError('Invalid neighbor selector')
            offset = reference['offset']
            if offset < 0:
                raise ValueError('Invalid neighbor offset')
        except ValueError as error:
            raise GraphContextError('Некорректный курсор связей.', 400) from error
        if reference['scope'] != scope or offset > len(matching):
            raise GraphContextError('Область или ревизия связей изменилась; начните чтение заново.', 409)
    result = {**diagnostic_summary(graph), "snapshotId": graph.get("metadata", {}).get("snapshotId"), "nodeId": node_id,
              "analysisContext": analysis_context(graph),
              "nodes": [_compact_node(by_id[node_id])], "edges": [], "total": len(matching),
              "truncated": len(matching) - offset > limit,
              'pageOffset': offset, 'pageHasMore': False, 'nextRef': None}
    result["truncated"] |= result["nodes"][0].get("truncated", False)
    if _size(result) > MAX_RESPONSE_BYTES:
        raise GraphContextError("Идентификатор узла превышает бюджет ответа.", 413)
    included = {node_id}
    for edge in matching[offset:offset + limit]:
        copied, shortened = _bounded_value(edge)
        if shortened:
            copied["truncated"] = True
        additions = []
        for endpoint in (edge["source"], edge["target"]):
            if endpoint not in included and not any(node["id"] == endpoint for node in additions):
                additions.append(_compact_node(by_id[endpoint]))
        candidate = {**result, "nodes": result["nodes"] + additions, "edges": result["edges"] + [copied]}
        following = offset + len(candidate['edges'])
        candidate['pageHasMore'] = following < len(matching)
        try:
            candidate['nextRef'] = encode_reference({'scope': scope, 'offset': following}) if candidate['pageHasMore'] else None
        except ValueError as error:
            raise GraphContextError(str(error), 413) from error
        if _size(candidate) > MAX_RESPONSE_BYTES:
            result["truncated"] = True
            break
        result = candidate
        included.update(node["id"] for node in additions)
        result["truncated"] |= shortened or any(node.get("truncated", False) for node in additions)
    if offset < len(matching) and not result['edges']:
        raise GraphContextError('Одно ребро с обязательными идентификаторами превышает бюджет ответа.', 413)
    return result
