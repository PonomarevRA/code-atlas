"""Ограниченные чтения SQLite и курсоры, закреплённые за картой/снимком/запросом."""

import base64
from decimal import Decimal, InvalidOperation
import json
import re
import sqlite3
import time

from .data_models import DataError, MAX_RESPONSE_BYTES, require
from .data_storage import encode, identity, typed


def read_database(path):
    """Открывает опубликованную БД только для чтения; SQL прерывается по бюджету."""
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    deadline = time.monotonic() + 5
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return connection


def scope_key(map_id, query, operation):
    """Курсор одной операции нельзя продолжить в другой коллекции или снимке."""
    value = query.model_dump(exclude={"cursor"})
    return identity(map_id, operation, value)


def offset(cursor, scope):
    """Декодирует только ограниченный JSON; курсор не содержит SQL или пути."""
    if cursor is None:
        return 0
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        require(value.get("scope") == scope and type(value.get("offset")) is int and 0 <= value["offset"] <= 10**12,
                "INVALID_CURSOR", "Курсор относится к другой карте, операции или снимку.", 409)
        return value["offset"]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise DataError("INVALID_CURSOR", "Некорректный курсор.", 409) from None


def cursor(scope, number):
    """Создаёт переносимое продолжение с проверяемой областью."""
    return base64.urlsafe_b64encode(encode({"scope": scope, "offset": number}).encode()).decode()


def compact(value, text_limit=1200):
    """Сокращает длинные строки в представлении, явно отмечая усечение."""
    if isinstance(value, str):
        return value[:text_limit]
    if isinstance(value, list):
        return [compact(item, text_limit) for item in value[:100]]
    if isinstance(value, dict):
        result = {key: compact(item, text_limit) for key, item in value.items()}
        if encode(result) != encode(value):
            result["contentTruncated"] = True
        return result
    return value


def page(items, map_id, query, operation, total=None):
    """Пагинация учитывает байты, включая первую большую запись; не теряет продолжение."""
    scope = scope_key(map_id, query, operation)
    start = offset(query.cursor, scope)
    selected = []
    consumed = 0
    for item in items[start:start + query.limit]:
        safe = item
        if len(encode(safe).encode()) > MAX_RESPONSE_BYTES - 4000:
            safe = compact(item)
            if len(encode(safe).encode()) > MAX_RESPONSE_BYTES - 4000:
                safe = {key: compact(item[key], 300) for key in ("id", "kind", "label", "collectionId") if key in item}
                if "provenance" in item:
                    safe["provenance"] = {key: compact(value, 300) for key, value in item["provenance"].items()
                                          if key in {"file", "sourceHash", "line", "endLine", "pointer", "sourceKind"}}
                safe.update(contentTruncated=True, warning="Запросите выбранные поля или более узкую область.")
        if len(encode(selected + [safe]).encode()) > MAX_RESPONSE_BYTES - 4000:
            break
        selected.append(safe)
        consumed += 1
    count = len(items) if total is None else total
    more = start + consumed < count
    return {"mapId": map_id, "snapshotId": query.snapshotId, "items": selected, "total": count,
            "truncated": more or any(item.get("contentTruncated") for item in selected),
            "nextCursor": cursor(scope, start + consumed) if more else None}


def record(row, fields=None):
    """Возвращает точные значения и типы из снимка; числа Decimal представлены строками."""
    values, provenance = json.loads(row["payload"]), json.loads(row["provenance"])
    if fields:
        chosen = {}
        types = {}
        for field in fields:
            current = values
            for part in field.split("/")[1:]:
                key = part.replace("~1", "/").replace("~0", "~")
                current = current.get(key) if isinstance(current, dict) else None
            if field in provenance.get("valueTypes", {}):
                chosen[field] = current
                types[field] = provenance["valueTypes"][field]
        values = chosen
        provenance["valueTypes"] = types
        provenance["collectionReferences"] = {key: value for key, value in provenance.get("collectionReferences", {}).items() if key in fields}
    return {"id": row["id"], "collectionId": row["collection"], "ordinal": row["ordinal"],
            "values": values, "provenance": provenance, "parentRecordId": row["parent"]}


def filters_sql(filters, collection, collections):
    """Компилирует allowlisted DSL в параметризованный EXISTS по индексам."""
    clauses, parameters = [], []
    available = {field["name"] for field in collections[collection]["fields"] if field["indexed"]}
    for index, item in enumerate(filters):
        require(isinstance(item, dict) and set(item) <= {"field", "op", "value", "type"}, "INVALID_FILTER", "Некорректные поля фильтра.")
        field, operation = item.get("field"), item.get("op", "eq")
        require(field in available, "UNINDEXED_FIELD", "Поле не индексировано; выберите indexFields при построении.")
        require(operation in {"eq", "ne", "isNull", "missing"}, "INVALID_FILTER", "Поддерживаются eq, ne, isNull и missing.")
        alias = f"c{index}"
        condition = f"{alias}.record=r.id AND {alias}.collection=? AND {alias}.field=?"
        parameters.extend([collection, field])
        if operation in {"eq", "ne"}:
            value = item.get("value")
            require(value is None or isinstance(value, (str, int, bool)), "INVALID_FILTER", "Значение фильтра — строка, int, bool или null; decimal передавайте строкой с type=number.")
            if item.get("type") == "number":
                try:
                    value = Decimal(str(value))
                    require(value.is_finite() and len(str(value)) <= 1024, "INVALID_FILTER", "Некорректное число.")
                except InvalidOperation:
                    raise DataError("INVALID_FILTER", "Некорректное число.") from None
            else:
                require(item.get("type") in {None, "string", "boolean", "null"}, "INVALID_FILTER", "Неизвестный тип фильтра.")
            kind, key = typed(value)
            require(item.get("type") in {None, kind}, "INVALID_FILTER", "Значение не соответствует явно указанному типу.")
            condition += f" AND {alias}.type=? AND {alias}.value=?"
            parameters.extend([kind, key])
        elif operation == "isNull":
            condition += f" AND {alias}.type='null'"
        prefix = "NOT " if operation in {"ne", "missing"} else ""
        clauses.append(f"{prefix}EXISTS(SELECT 1 FROM cells {alias} WHERE {condition})")
    return (" AND " + " AND ".join(clauses)) if clauses else "", parameters


def query_records(connection, metadata, map_id, query):
    """Запрашивает до 51 строки без COUNT(*) полного набора и с byte-aware курсором."""
    collection = query.collectionId
    collections = metadata["collections"]
    require(collection in collections, "COLLECTION_MISSING", "Коллекция не найдена.", 404)
    scope = scope_key(map_id, query, "records")
    start = offset(query.cursor, scope)
    condition, parameters = filters_sql(query.filters, collection, collections)
    rows = connection.execute("SELECT r.* FROM records r WHERE r.collection=? AND r.ordinal>?" + condition + " ORDER BY r.ordinal LIMIT ?",
                              [collection, start, *parameters, query.limit + 1]).fetchall()
    selected, last = [], start
    for row in rows[:query.limit]:
        item = record(row, query.fields)
        if len(encode(item).encode()) > MAX_RESPONSE_BYTES - 4000:
            item = {"id": row["id"], "collectionId": collection, "ordinal": row["ordinal"], "values": {},
                    "provenance": {key: compact(value, 4096) for key, value in item["provenance"].items()
                                   if key in {"file", "sourceHash", "line", "endLine", "pointer", "sourceKind", "scenarioId", "role"}},
                    "availableFields": list(json.loads(row["provenance"]).get("valueTypes", {}))[:20],
                    "contentTruncated": True, "warning": "Запросите fields через get_data_record."}
        if len(encode(selected + [item]).encode()) > MAX_RESPONSE_BYTES - 4000:
            break
        selected.append(item)
        last = row["ordinal"]
    more = len(rows) > len(selected)
    return {"mapId": map_id, "snapshotId": query.snapshotId, "items": selected,
            "truncated": more or any(item.get("contentTruncated") for item in selected),
            "nextCursor": cursor(scope, last) if more else None}


def search_scenarios(connection, map_id, query):
    """SQLite выбирает только страницу кратких описаний, не все AST со шагами."""
    scope = scope_key(map_id, query, "scenarios")
    start = offset(query.cursor, scope)
    pattern = "%" + query.query + "%"
    rows = connection.execute("""SELECT id,name,substr(json_extract(payload,'$.description'),1,1200) AS description,
            json_extract(payload,'$.source') AS source,tags,json_extract(payload,'$.kind') AS kind
        FROM scenarios WHERE (name LIKE ? OR payload LIKE ?)
        AND (?='' OR EXISTS(SELECT 1 FROM json_each(scenarios.tags) WHERE value=?))
        ORDER BY id LIMIT ? OFFSET ?""", (pattern, pattern, query.tag, query.tag, query.limit + 1, start)).fetchall()
    values = [{"id": row["id"], "name": row["name"][:1200], "description": row["description"],
               "source": json.loads(row["source"]), "tags": json.loads(row["tags"]), "kind": row["kind"]} for row in rows]
    result = page(values, map_id, query.model_copy(update={"cursor": None}), "scenarios")
    result["total"] = None
    result["nextCursor"] = cursor(scope, start + len(result["items"])) if len(values) > len(result["items"]) else None
    return result


def get_scenario(connection, map_id, query):
    """Читает шаблон или выбранный пример; не компилирует и не запускает все варианты."""
    row = connection.execute("SELECT payload FROM scenarios WHERE id=?", (query.scenarioId,)).fetchone()
    require(row is not None, "SCENARIO_MISSING", "Сценарий не найден.", 404)
    value = json.loads(row[0])
    substitutions = {}
    if query.exampleId:
        example = connection.execute("SELECT * FROM records WHERE id=?", (query.exampleId,)).fetchone()
        require(example is not None and json.loads(example["provenance"]).get("scenarioId") == query.scenarioId
                and example["collection"] in {item["collectionId"] for item in value["examples"]},
                "EXAMPLE_MISMATCH", "Пример относится к другому сценарию.", 409)
        substitutions = json.loads(example["payload"])
    steps = []
    for background in value.get("background", []):
        for step in background.get("steps", []):
            steps.append({**step, "backgroundOwner": background["owner"], "source": background["source"]})
    steps.extend(value["steps"])
    unresolved = set()

    def substitute(item):
        """Подстановки являются текстом; placeholder-строка не вычисляется как выражение."""
        if isinstance(item, str):
            def replacement(match):
                name = match.group(1)
                if name not in substitutions:
                    unresolved.add(name)
                return str(substitutions.get(name, match.group(0)))
            return re.sub(r"<([^<>]+)>", replacement, item)
        if isinstance(item, list):
            return [substitute(child) for child in item]
        if isinstance(item, dict):
            return {key: substitute(child) if key in {"text", "content", "value", "docString", "dataTable", "rows", "cells"} else child
                    for key, child in item.items()}
        return item
    shown = [dict(step, resolved=substitute(step)) if query.exampleId else step for step in steps]
    result = page(shown, map_id, query, "scenario")
    result["scenario"] = compact({key: item for key, item in value.items() if key not in {"steps", "background", "comments"}}, 800)
    result["comments"] = compact(value.get("comments", [])[:10], 800)
    result["unresolvedParameters"] = [name[:100] for name in sorted(unresolved)[:10]]
    result["unresolvedParameterCount"] = len(unresolved)
    result["runtimeStatus"] = "unknown"
    # Even metadata can be long: never return a silent oversized MCP message.
    if len(encode(result).encode()) > MAX_RESPONSE_BYTES - 3000:
        result["scenario"] = {"id": value["id"], "name": value["name"][:200], "source": value["source"], "contentTruncated": True}
        result["comments"] = []
    return result
