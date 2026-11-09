"""Короткие фактические request/response summaries без исходников и повторного выполнения."""

import json
import re

from .events import text

RESULT_FIELDS = ("nodes", "results", "items", "matches", "projects", "documents")
TOOLS = {"search-nodes": "search_nodes", "graph-node": "get_node", "node-description": "describe_node",
         "graph-neighbors": "get_neighbors"}


def request_summary(path, context):
    """Описывает реально вызванный HTTP adapter и ограниченные разрешённые параметры."""
    route = path.rstrip("/").rsplit("/", 1)[-1]
    tool = TOOLS.get(route, text(route, 80))
    query = context["query"].get("preview")
    target = context["target"].get("nodeId")
    detail = query or target or context["target"].get("projectId") or ""
    return {"tool": tool, "summary": text(f"{tool}: {detail}", 512)}


def _item_summary(value):
    """Проецирует один реальный result object, исключая code/text/body и внутренние пути."""
    if not isinstance(value, dict):
        return None
    label = value.get("label") or value.get("name") or value.get("title") or value.get("id")
    if not isinstance(label, str):
        return None
    result = {"label": text(label, 128)}
    for source, destination in (("file", "file"), ("id", "nodeId")):
        field = value.get(source)
        if isinstance(field, str):
            result[destination] = text(field, 128)
    return result


def _prefix_items(raw):
    """Читает максимум три полных элемента первого root array из ограниченного начала ответа."""
    match = re.match(r'\s*\{\s*"(?:nodes|results|items|matches|projects|documents)"\s*:\s*\[', raw)
    if not match:
        return []
    decoder, offset, result = json.JSONDecoder(), match.end(), []
    for _ in range(3):
        while offset < len(raw) and raw[offset] in " \t\r\n,":
            offset += 1
        try:
            item, end = decoder.raw_decode(raw, offset)
        except ValueError:
            break
        summary = _item_summary(item)
        if summary:
            result.append(summary)
        offset = end
    return result


def response_summary(raw, total_bytes, oversized, status, error):
    """Возвращает фактические count/до трёх результатов; неполный обзор помечается truncated."""
    result = {"summary": f"HTTP {status}; получено {total_bytes} байт", "items": [],
              "truncated": bool(oversized), "errorCode": error}
    if status >= 400:
        result["summary"] = f"HTTP {status}; операция завершилась ошибкой"
        return result
    try:
        value = json.loads(raw) if not oversized else None
    except (ValueError, UnicodeError):
        value = None
    if isinstance(value, dict):
        for field in RESULT_FIELDS:
            if not isinstance(value.get(field), list):
                continue
            rows = value[field]
            count = value.get("total")
            if type(count) is not int or count < 0:
                count = len(rows)
            result["resultCount"] = count
            for row in rows[:3]:
                item = _item_summary(row)
                if item:
                    result["items"].append(item)
            result["truncated"] = bool(value.get("truncated")) or len(rows) > 3
            result["summary"] = f"Получено результатов: {count}"
            return result
        single = value.get("node") if isinstance(value.get("node"), dict) else value
        item = _item_summary(single)
        if item:
            result["items"] = [item]
            result["summary"] = f"Прочитано: {item['label']}"
        elif isinstance(value.get("source"), str):
            result["summary"] = f"Прочитан исходный код; ответ {total_bytes} байт"
        else:
            keys = [text(key, 32) for key in list(value)[:5]]
            result["summary"] = text(f"Получен объект: {', '.join(keys)}; {total_bytes} байт", 512)
        # Only documented semantic fields are previewed; source/text/code bodies stay out.
        for field in ("factualSummary", "summary", "description", "comment"):
            excerpt = value.get(field)
            if isinstance(excerpt, str) and excerpt.strip():
                result["summary"] = text(f"{result['summary']}. {text(excerpt, 256)}", 512)
                result["truncated"] = result["truncated"] or len(excerpt) > 256
                break
    elif oversized:
        result["items"] = _prefix_items(raw.decode("utf-8", errors="replace"))
        result["summary"] += "; обзор ответа усечён"
    return result
