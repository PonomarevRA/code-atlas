"""Компактная структура и локальный подграф записей, совместимые с nodes/links."""

from collections import deque
import json

from .data_models import MAX_RESPONSE_BYTES, require
from .data_queries import compact, record, cursor, offset, scope_key
from .data_storage import encode, identity


def schema_nodes(metadata, parent=None):
    """В схеме представлены файлы/коллекции/поля, а не миллионы строк данных."""
    map_id = metadata["mapId"]
    root = "data:" + map_id
    nodes = [{"id": root, "kind": "dataset", "label": "Карта данных", "owner": None, "count": metadata["records"]}]
    file_ids = {}
    for file in metadata["files"]:
        file_id = root + ":file:" + identity(file["path"])
        file_ids[file["path"]] = file_id
        nodes.append({"id": file_id, "owner": root, "kind": "data-file", "label": file["path"],
                      "sizeBytes": file["sizeBytes"], "sourceHash": file["sha256"]})
    for collection in metadata["collections"].values():
        nodes.append({key: value for key, value in dict(collection, owner=collection.get("owner") or file_ids[collection["file"]]).items() if key != "fields"})
        for field in collection["fields"]:
            nodes.append({**field, "id": collection["id"] + ":field:" + identity(field["name"]), "kind": "data-field",
                          "label": field["name"], "owner": collection["id"]})
    for node in metadata["gherkinNodes"]:
        value = dict(node)
        value["owner"] = node.get("owner") or file_ids[node["source"]["file"]]
        nodes.append(value)
    if parent is not None:
        nodes = [node for node in nodes if node.get("owner") == parent or node["id"] == parent]
    return nodes


def schema_graph(metadata):
    """Экспорт Graphify содержит только структуру и проверенные правила отношений."""
    nodes = schema_nodes(metadata)
    links = [{"source": node["owner"], "target": node["id"], "relation": "contains", "confidence": "EXTRACTED"}
             for node in nodes if node.get("owner")]
    for rule in metadata["relations"]["items"]:
        links.append({"source": rule["sourceCollection"], "target": rule["targetCollection"], "relation": "value_match",
                      "id": rule["id"], "semanticStatus": rule["semanticStatus"], "coverage": rule["coverage"],
                      "confidence": "INFERRED" if rule["status"] == "candidate" else "EXTRACTED"})
    return {"directed": True, "multigraph": True, "nodes": nodes, "links": links,
            "graph": {"provider": "atlas-data-maps", "mapId": metadata["mapId"], "snapshotId": metadata["snapshotId"],
                      "recordProjection": "on-demand", "candidateSearchTruncated": metadata["relations"]["truncated"]}}


def neighbors(connection, row, rules):
    """Перечисляет только ID соседей; крупные payload загружаются после лимита графа."""
    if row["parent"]:
        yield row["parent"], "parent_record", row["id"], row["parent"]
    for child in connection.execute("SELECT id FROM records WHERE parent=? ORDER BY id", (row["id"],)):
        yield child["id"], "parent_record", child["id"], row["id"]
    for rule in rules:
        if row["collection"] not in {rule["sourceCollection"], rule["targetCollection"]}:
            continue
        incoming = row["collection"] == rule["targetCollection"]
        selected_field = rule["targetField"] if incoming else rule["sourceField"]
        other_field = rule["sourceField"] if incoming else rule["targetField"]
        other_collection = rule["sourceCollection"] if incoming else rule["targetCollection"]
        key = connection.execute("SELECT type,value,raw FROM cells WHERE record=? AND field=? AND type!='null'",
                                 (row["id"], selected_field)).fetchone()
        if key is None:
            continue
        condition = "c.type=? AND c.value=?" if rule["compareAs"] == "typed" else "c.raw=?"
        params = [key["type"], key["value"]] if rule["compareAs"] == "typed" else [key["raw"]]
        matches = connection.execute("SELECT c.record FROM cells c WHERE c.collection=? AND c.field=? AND "
                                     + condition + " ORDER BY c.record", [other_collection, other_field, *params])
        for other in matches:
            source, target = (other["record"], row["id"]) if incoming else (row["id"], other["record"])
            yield other["record"], rule["id"], source, target


def trace_links(connection, metadata, map_id, query):
    """Ограниченный BFS; cursor продолжает fan-out начальной записи без потери соседей."""
    first = connection.execute("SELECT * FROM records WHERE id=?", (query.recordId,)).fetchone()
    require(first is not None, "RECORD_MISSING", "Стартовая запись не найдена.", 404)
    require(query.maxNodes >= 2 or query.maxDepth == 0, "INVALID_GRAPH_LIMIT", "Для связей нужны минимум два узла.")
    scope = scope_key(map_id, query, "trace")
    start = offset(query.cursor, scope)
    rules = metadata["relations"]["items"]
    if query.relationIds:
        require(set(query.relationIds) <= {rule["id"] for rule in rules}, "RELATION_MISSING", "Правило не найдено в этом снимке.")
        rules = [rule for rule in rules if rule["id"] in query.relationIds]
    nodes, edges, seen_edges = {}, [], set()
    queued = {first["id"]}
    queue = deque([(first["id"], 0)])
    continuations = []
    while queue:
        record_id, depth = queue.popleft()
        row = connection.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            continue
        value = compact(record(row, query.fields), 200)
        if len(encode(value).encode()) > 1800:
            source = json.loads(row["provenance"])
            value = {"id": row["id"], "collectionId": row["collection"], "contentTruncated": True,
                     "provenance": {key: compact(item, 300) for key, item in source.items()
                                    if key in {"file", "sourceHash", "line", "pointer", "sourceKind"}}}
        nodes[row["id"]] = {**value, "label": metadata["collections"][row["collection"]]["label"][:120] + f" · {row['ordinal']}", "kind": "data-record"}
        if depth >= query.maxDepth:
            continue
        skipped = start if row["id"] == query.recordId else 0
        consumed = 0
        for other_id, relation, source, target in neighbors(connection, row, rules):
            if consumed < skipped:
                consumed += 1
                continue
            key = (source, target, relation)
            if key in seen_edges:
                consumed += 1
                continue
            if (other_id not in queued and len(queued) >= query.maxNodes) or len(edges) >= 300:
                continuation_query = query.model_copy(update={"recordId": row["id"], "cursor": None})
                continuations.append({"recordId": row["id"], "cursor": cursor(scope_key(map_id, continuation_query, "trace"), consumed)})
                break
            consumed += 1
            seen_edges.add(key)
            edges.append({"source": source, "target": target, "relation": relation})
            if other_id not in queued:
                queued.add(other_id)
                queue.append((other_id, depth + 1))
    edges = [edge for edge in edges if edge["source"] in nodes and edge["target"] in nodes]
    result = {"mapId": map_id, "snapshotId": query.snapshotId, "nodes": list(nodes.values()), "links": edges,
              "truncated": bool(continuations), "continuations": continuations,
              "continuationSeeds": [item["recordId"] for item in continuations], "runtimeStatus": "unknown",
              "nextCursor": next((item["cursor"] for item in continuations if item["recordId"] == query.recordId), None)}
    while len(encode(result).encode()) > MAX_RESPONSE_BYTES - 4000:
        removed = result["nodes"].pop()
        result["links"] = [edge for edge in result["links"] if removed["id"] not in {edge["source"], edge["target"]}]
        result["truncated"] = True
        result["warning"] = "Уменьшите maxNodes для полного локального подграфа в байтовом бюджете."
    return result
