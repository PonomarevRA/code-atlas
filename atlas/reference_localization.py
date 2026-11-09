"""Выбор перевода отдельного документа с сохранением английского оригинала."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

from .reference_documents import ReferenceError


def captured_records(text: str) -> list[dict]:
    """Проверяет подготовленные текстовые страницы; идентичность задаёт исходный путь/API."""
    try:
        rows = json.loads(text)
    except ValueError:
        raise ReferenceError("Invalid captured reference JSON") from None
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ReferenceError("Expected up to 1000 captured documents")
    for row in rows:
        if not isinstance(row, dict):
            raise ReferenceError("Invalid captured document")
        for key, maximum in (("path", 2000), ("title", 500), ("symbol", 500),
                             ("signature", 2000), ("text", 4 * 1024 * 1024), ("sourceUrl", 2000)):
            if not isinstance(row.get(key), str) or len(row[key]) > maximum:
                raise ReferenceError("Invalid captured field: " + key)
        path = PurePosixPath(row["path"])
        if path.is_absolute() or ".." in path.parts or "\\" in row["path"]:
            raise ReferenceError("Unsafe captured source path")
        if not row["sourceUrl"].startswith("https://"):
            raise ReferenceError("Expected HTTPS captured provenance")
        aliases = row.get("aliases", [])
        # Целая страница Python может содержать свыше тысячи API anchors.
        if not isinstance(aliases, list) or len(aliases) > 2000 or any(not isinstance(a, str) or len(a) > 2000 for a in aliases):
            raise ReferenceError("Invalid captured aliases")
    return rows


def family(pack: dict) -> tuple:
    """Перевод сопоставляется только с той же технологией, версией, редакцией и корпусом."""
    return tuple(pack[key] for key in ("language", "version", "edition", "title"))


def prefer_documents(store, hits: list[dict], packs: list[dict], locale: str) -> list[dict]:
    """Подменяет кандидата существующим переводом по стабильному ID; отсутствие не ошибка."""
    by_id = {pack["id"]: pack for pack in packs}
    preferred = {}
    for pack in packs:
        if pack["locale"] == locale:
            preferred.setdefault(family(pack), pack)
    result = {}
    for hit in hits:
        pack = by_id[hit["packId"]]
        identity = (hit["symbol"], hit["signature"]) if hit["signature"] else hit["documentId"]
        key = (family(pack), identity)
        target = preferred.get(family(pack))
        candidate = hit
        if target and target["id"] != pack["id"]:
            try:
                doc = store.corresponding_document(target, hit)
            except ReferenceError as error:
                if error.status != 404:
                    raise
            else:
                candidate = {**hit, "packId": target["id"], "documentId": doc["id"], "title": doc["title"],
                             "excerpt": doc["summary"] or doc["text"],
                             "excerptScope": "author-summary" if doc["summary"] else "document-head",
                             "excerptTruncated": doc["summaryTruncated"] if doc["summary"] else doc["nextOffset"] is not None,
                             "locale": target["locale"], "sourceUrl": doc["sourceUrl"], "sourcePath": doc["path"]}
        candidate = {**candidate, "requestedLocale": locale, "localeFallback": candidate["locale"] != locale}
        previous = result.get(key)
        if previous is None or candidate["rankScore"] > previous["rankScore"]:
            result[key] = candidate
    return list(result.values())
