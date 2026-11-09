"""Чистая ограниченная MCP-проекция coverage: без IO, кэша или исполнения тестов.

analysisId связывает страницы со всем результатом текущего анализа, включая Gherkin.
Счётчики отчётов, объявленные skip и статические связи остаются разными фактами.
"""
import hashlib
import json
from urllib.parse import urlencode, urlsplit

DATA_BUDGET_BYTES = 30_000
ENVELOPE_BUDGET_BYTES = 64_000
SECTIONS = {"summary", "conditions", "tests"}
KINDS = {"all", "if", "switch", "loop"}
RUNTIME_STATUSES = {"all", "unknown", "partial", "uncovered", "covered"}
TEST_KINDS = {"all", "unit", "gherkin"}
LOOPS = {"for", "foreach", "while", "do"}


def _choice(value, choices, name):
    """Не приводит контейнеры/числа к строке: ошибочный запрос должен быть исправлен явно."""
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"Некорректный {name}.")
    return value


def _optional_id(value, name):
    """Разрешает bounded exact ID; строки не интерпретируются как запрос/путь."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(char) < 32 for char in value):
        raise ValueError(f"Некорректный {name}.")
    return value


def validate_coverage_query(section="summary", query="", kind="all", runtimeStatus="all", testKind="all",
                            offset=0, limit=20, conditionId=None, testId=None, analysisId=None, reportPaths=None) -> dict:
    """Валидирует фильтры/курсор и ≤8 относительных report paths; ничего не читает.

    Детальный ID выбирает свой section при default summary. Деталь точная и не
    фильтруется текстом/статусом; фильтры работают только для постраничных списков.
    bool не принимается вместо offset/limit; limit 1..40, offset 0..100_000.
    """
    section = _choice(section, SECTIONS, "section")
    kind = _choice(kind, KINDS, "kind")
    runtimeStatus = _choice(runtimeStatus, RUNTIME_STATUSES, "runtimeStatus")
    testKind = _choice(testKind, TEST_KINDS, "testKind")
    if not isinstance(query, str) or len(query) > 500 or "\0" in query:
        raise ValueError("query должен быть строкой не длиннее 500 символов.")
    if type(offset) is not int or not 0 <= offset <= 100_000:
        raise ValueError("offset должен быть целым числом 0..100000.")
    if type(limit) is not int or not 1 <= limit <= 40:
        raise ValueError("limit должен быть целым числом 1..40.")
    conditionId = _optional_id(conditionId, "conditionId")
    testId = _optional_id(testId, "testId")
    analysisId = _optional_id(analysisId, "analysisId")
    if conditionId and testId:
        raise ValueError("Укажите conditionId или testId, а не оба.")
    detail_section = "conditions" if conditionId else "tests" if testId else None
    if detail_section:
        if section not in {"summary", detail_section}:
            raise ValueError("Детальный ID не соответствует section.")
        section = detail_section
    if reportPaths is None:
        reportPaths = []
    if not isinstance(reportPaths, list) or len(reportPaths) > 8:
        raise ValueError("reportPaths: не более 8 относительных путей.")
    paths = []
    for path in reportPaths:
        if not isinstance(path, str) or not path or len(path) > 1024 or any(ord(char) < 32 for char in path):
            raise ValueError("Некорректный путь отчёта.")
        normalized = path.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
            raise ValueError("Отчёт должен иметь относительный путь внутри repository.")
        paths.append(normalized)
    return {"section": section, "query": query.strip(), "kind": kind, "runtimeStatus": runtimeStatus,
            "testKind": testKind, "offset": offset, "limit": limit, "conditionId": conditionId,
            "testId": testId, "analysisId": analysisId, "reportPaths": paths}


def _rendered_size(data: dict) -> tuple[int, int]:
    """Оценивает и ASCII indent2 text, и дублированный MCP envelope; не полагается на compact JSON."""
    rendered = json.dumps(data, ensure_ascii=True, indent=2)
    envelope = {"content": [{"type": "text", "text": rendered}], "structuredContent": data, "isError": False}
    return len(rendered.encode("utf-8")), len(json.dumps(envelope, ensure_ascii=True).encode("utf-8"))


def _fits(data: dict) -> bool:
    """Проверяет обе границы: response text ≤30k и полный duplicate envelope ≤64k."""
    rendered, envelope = _rendered_size(data)
    return rendered <= DATA_BUDGET_BYTES and envelope <= ENVELOPE_BUDGET_BYTES


class _Compact:
    """Копирует выбранные поля, сохраняя явные пути всех сокращённых данных."""
    def __init__(self):
        self.omitted = []

    def text(self, value, path, limit=512):
        """Ограничивает текст по символам; ASCII envelope дополнительно проверяется целиком."""
        if value is None:
            return None
        if not isinstance(value, str):
            self.omitted.append(path)
            return None
        if len(value) > limit:
            self.omitted.append(path)
        return value[:limit]

    def identity(self, value, path):
        """Идентичность нельзя сокращать: допустимый ID точный, oversized ID явно недоступен."""
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 1024:
            self.omitted.append(path)
            return None
        return value

    def fields(self, source, fields, prefix="", text_limit=512):
        """Копирует scalar-поля; вложенный graph/source payload не переносится случайно."""
        result = {}
        if not isinstance(source, dict):
            return result
        for key in fields:
            if key not in source:
                continue
            value = source[key]
            path = f"{prefix}.{key}" if prefix else key
            if key == "id" or key.endswith("Id"):
                result[key] = self.identity(value, path)
            elif isinstance(value, str):
                result[key] = self.text(value, path, text_limit)
            elif value is None or type(value) in {bool, int, float}:
                result[key] = value
            else:
                self.omitted.append(path)
        return result

    def texts(self, values, path, count=8, limit=200):
        """Сохраняет ограниченный prefix list и отмечает omitted tail."""
        if not isinstance(values, list):
            return []
        if len(values) > count:
            self.omitted.append(path)
        return [self.text(value, f"{path}[{index}]", limit) for index, value in enumerate(values[:count])]

    def identities(self, values, path, count=8):
        """Список содержит только точные usable ID; исключённые элементы перечисляются в omissions."""
        if not isinstance(values, list):
            return []
        if len(values) > count:
            self.omitted.append(path)
        result = []
        for index, value in enumerate(values[:count]):
            identifier = self.identity(value, f"{path}[{index}]")
            if identifier is not None:
                result.append(identifier)
        return result


def _runtime(source: dict, compact: _Compact, path="runtime") -> dict:
    """Копирует отчётную интерпретацию без вывода результата конкретного теста/цели."""
    runtime = compact.fields(source, ("status", "lineHits", "branchesCovered", "branchesValid", "reason", "freshness", "basis", "reportedLines"), path, 700)
    runtime["reportPaths"] = compact.texts(source.get("reportPaths", []), path + ".reportPaths", 8, 1024)
    return runtime


def _condition(source: dict, tests: dict, detail=False) -> dict:
    """Компактная condition card: runtime и objectives отдельно, 2 representative links без graph paths."""
    compact = _Compact()
    result = compact.fields(source, ("id", "ownerNodeId", "methodId", "methodLabel", "namespace", "file", "line", "column", "endLine", "kind", "sourceStatus", "conditionTruncated", "runtimeAmbiguous", "testLinksTotal", "testLinksTruncated"), text_limit=1024)
    result["condition"] = compact.text(source.get("condition", ""), "condition", 1000)
    result["runtime"] = _runtime(source.get("runtime", {}), compact)
    result["testKinds"] = compact.fields(source.get("testKinds", {}), ("unit", "gherkin"), "testKinds")
    objectives = source.get("objectives", [])
    cap = 16 if detail else 6
    result["objectives"] = [compact.fields(item, ("id", "kind", "label", "implicit", "runtimeStatus"), f"objectives[{index}]", 160) for index, item in enumerate(objectives[:cap])]
    result["objectivesTotal"] = len(objectives)
    if len(objectives) > cap:
        compact.omitted.append("objectives")
    result["testLinks"] = []
    for index, link in enumerate(source.get("testLinks", [])[:2]):
        prefix = f"testLinks[{index}]"
        record = compact.fields(link, ("testId", "testKind", "targetNodeId", "confidence", "sourceKind", "pathLength", "pathTruncated"), prefix, 1024)
        test = tests.get(link.get("testId"), {})
        record["test"] = compact.fields(test, ("id", "name", "kind", "framework", "declaredStatus", "file", "line"), prefix + ".test", 512)
        record["relations"] = compact.texts(link.get("relations", []), prefix + ".relations", 12, 64)
        if isinstance(link.get("bindingEvidence"), dict):
            record["bindingEvidence"] = compact.fields(link["bindingEvidence"], ("nodeId", "file", "line", "pattern", "ambiguous", "sourceKind"), prefix + ".bindingEvidence", 512)
        result["testLinks"].append(record)
    if len(source.get("testLinks", [])) > 2:
        compact.omitted.append("testLinks")
    result["proofPathsIncluded"] = False
    result["fieldsOmitted"] = list(dict.fromkeys(compact.omitted))
    return result


def _test(source: dict, detail=False) -> dict:
    """Сохраняет declared skip и unknown execution; деталь содержит bounded Gherkin steps/bindings."""
    compact = _Compact()
    result = compact.fields(source, ("id", "nodeId", "kind", "subtype", "framework", "frameworkResolution", "name", "namespace", "feature", "rule", "file", "line", "column", "endLine", "sourceStatus", "declaredStatus", "exampleRows", "exampleRowsTruncated", "staticTargetsTotal", "staticTargetsTruncated"), text_limit=1024)
    result["runtime"] = _runtime(source.get("runtime", {}), compact)
    result["skipReasons"] = compact.texts(source.get("skipReasons", []), "skipReasons", 8 if detail else 3, 256)
    result["tags"] = compact.texts(source.get("tags", []), "tags", 16 if detail else 8, 64)
    result["staticTargetIds"] = compact.identities(source.get("staticTargetIds", []), "staticTargetIds", 8 if detail else 2)
    if detail:
        result["steps"] = []
        steps = source.get("steps", [])
        for index, step in enumerate(steps[:8]):
            prefix = f"steps[{index}]"
            record = compact.fields(step, ("text", "file", "line", "bindingStatus"), prefix, 512)
            bindings = step.get("bindings", [])
            record["bindings"] = [compact.fields(binding, ("nodeId", "file", "line", "pattern", "ambiguous", "sourceKind"), prefix + ".bindings", 512) for binding in bindings[:2]]
            if len(bindings) > 2:
                compact.omitted.append(prefix + ".bindings")
            result["steps"].append(record)
        result["stepsTotal"] = len(steps)
        if len(steps) > 8:
            compact.omitted.append("steps")
        examples = source.get("examples", [])
        result["examples"] = []
        for index, example in enumerate(examples[:4]):
            record = compact.fields(example, ("name", "line", "rows", "declaredStatus"), f"examples[{index}]", 256)
            record["tags"] = compact.texts(example.get("tags", []), f"examples[{index}].tags", 8, 64)
            result["examples"].append(record)
        result["examplesTotal"] = len(examples)
        if len(examples) > 4:
            compact.omitted.append("examples")
    result["fieldsOmitted"] = list(dict.fromkeys(compact.omitted))
    return result


def _matches(row: dict, query: dict, section: str, tests: dict) -> bool:
    """Фильтрует source facts; total testKinds важнее representative links при фильтре unit/Gherkin."""
    if query["runtimeStatus"] != "all" and row.get("runtime", {}).get("status", "unknown") != query["runtimeStatus"]:
        return False
    if section == "conditions":
        kind = "loop" if row.get("kind") in LOOPS else "switch" if str(row.get("kind", "")).startswith("switch") else row.get("kind")
        if kind == "conditional":
            kind = "if"
        if query["kind"] != "all" and kind != query["kind"]:
            return False
        requested = query["testKind"]
        if requested != "all":
            counts = row.get("testKinds", {})
            if requested in counts:
                if not isinstance(counts[requested], int) or counts[requested] <= 0:
                    return False
            elif not any(tests.get(link.get("testId"), {}).get("kind") == requested for link in row.get("testLinks", [])):
                return False
        text = [row.get(key, "") for key in ("condition", "methodLabel", "namespace", "file")]
        text.extend(tests.get(link.get("testId"), {}).get("name", "") for link in row.get("testLinks", []))
    else:
        if query["testKind"] != "all" and row.get("kind") != query["testKind"]:
            return False
        text = [row.get(key, "") for key in ("name", "namespace", "file", "feature", "rule")]
        text.extend(step.get("text", "") for step in row.get("steps", []))
    needle = query["query"].casefold()
    return not needle or any(needle in str(value).casefold() for value in text)


def _header(result: dict, query: dict, analysis_id: str, base_url: str) -> dict:
    """Сохраняет bounded summary/provenance; ссылка ведёт на существующую отдельную coverage page."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url должен указывать на локальный Atlas.")
    compact = _Compact()
    header = compact.fields(result, ("schemaVersion", "analyzerVersion", "snapshotId", "projectId", "sourceKind", "status"), text_limit=1024)
    header.update(schemaVersion=1, analysisId=analysis_id, section=query["section"])
    header["scope"] = compact.fields(result.get("scope", {}), ("nodeId", "mode"), "scope", 1024)
    header["summary"] = compact.fields(result.get("summary", {}), ("tests", "methods", "conditions", "namespaces", "reports"), "summary")
    for key, names in (("testsByKind", ("unit", "gherkin")), ("testsByDeclaredStatus", ("enabled", "disabled", "unknown")), ("conditionsByStatus", ("covered", "partial", "uncovered", "unknown"))):
        header["summary"][key] = compact.fields(result.get("summary", {}).get(key, {}), names, "summary." + key)
    header["truncation"] = {key: value for key, value in list(result.get("truncation", {}).items())[:24] if isinstance(key, str) and len(key) <= 64 and type(value) is bool}
    header["limits"] = {key: value for key, value in list(result.get("limits", {}).items())[:40] if isinstance(key, str) and len(key) <= 64 and type(value) is int}
    header["warnings"] = compact.texts(result.get("warnings", []), "warnings", 4, 350)
    header["reports"] = []
    for index, report in enumerate(result.get("reports", [])[:8]):
        record = compact.fields(report, ("id", "file", "status", "format", "fileCount", "lineCount", "mtimeNs", "runTimestampNs", "timestampInvalid", "sourceHash"), f"reports[{index}]", 512)
        record["warnings"] = compact.texts(report.get("warnings", []), f"reports[{index}].warnings", 2, 150)
        header["reports"].append(record)
    if len(result.get("reports", [])) > 8:
        compact.omitted.append("reports")
    params = [("repo", str(result.get("projectId", ""))), ("snapshot", str(result.get("snapshotId", "")))]
    if result.get("scope", {}).get("nodeId"):
        params.append(("node", str(result["scope"]["nodeId"])))
    for key, value in (("q", query["query"]), ("kind", query["kind"]), ("runtime", query["runtimeStatus"]), ("tests", query["testKind"]), ("condition", query["conditionId"])):
        if value and value != "all":
            params.append((key, value))
    params.extend(("report", path) for path in query["reportPaths"])
    header["coverageUrl"] = base_url.rstrip("/") + "/coverage.html?" + urlencode(params)
    header["query"] = {key: value for key, value in query.items() if key not in {"reportPaths", "analysisId"}}
    header["records"] = []
    header["pagination"] = {"offset": query["offset"], "limit": query["limit"], "total": 0, "emitted": 0, "nextOffset": None}
    header["projection"] = {"truncated": bool(compact.omitted), "recordOmissions": [], "fieldsOmitted": list(dict.fromkeys(compact.omitted)),
        "pageLimitedByBytes": False,
        "dataBudgetBytes": DATA_BUDGET_BYTES, "envelopeBudgetBytes": ENVELOPE_BUDGET_BYTES,
        "warning": "Компактная выборка: пути графа не включены. Покрытие отдельных тестов и целей не выводится из счётчиков отчёта."}
    return header


def _placeholder(row: dict, section: str) -> dict:
    """Сохраняет exact ID для oversized row, чтобы курсор продвигался на одну запись без потери её существования."""
    compact = _Compact()
    record = compact.fields(row, ("id", "kind", "sourceStatus"), text_limit=1024)
    record["runtime"] = compact.fields(row.get("runtime", {}), ("status",), "runtime", 32)
    if section == "tests":
        record["declaredStatus"] = row.get("declaredStatus", "unknown")
    record["fieldsOmitted"] = ["recordPayload"] + compact.omitted
    record["omissionReason"] = "Детали записи превышают MCP-бюджет; откройте coverageUrl."
    return record


def project_test_coverage(result: dict, querydict: dict, base_url: str) -> dict:
    """Фильтрует один текущий result и выдаёт bounded pages/detail; input не изменяет.

    Неверный analysisId выдаёт ValueError с новым ID. nextOffset основан на реально
    выданных записях. Не влезшая следующая запись остаётся началом следующей страницы.
    """
    if not isinstance(querydict, dict):
        raise ValueError("Запрос coverage должен быть объектом.")
    try:
        query = validate_coverage_query(**querydict)
    except TypeError as error:
        raise ValueError("Неизвестное поле запроса coverage.") from error
    canonical = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    analysis_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if query["analysisId"] is not None and query["analysisId"] != analysis_id:
        raise ValueError("Анализ изменился. Начните выборку заново с offset=0; analysisId=" + analysis_id)
    data = _header(result, query, analysis_id, base_url)
    tests = {test["id"]: test for test in result.get("tests", [])}
    section = query["section"]
    if section == "summary":
        if not _fits(data):
            _shrink_header(data)
        return data
    rows = result.get(section, [])
    detail_id = query["conditionId"] if section == "conditions" else query["testId"]
    if detail_id:
        selected = next((row for row in rows if row.get("id") == detail_id), None)
        if selected is None:
            raise ValueError("Точный ID не найден в текущем анализе: " + detail_id)
        record = _condition(selected, tests, True) if section == "conditions" else _test(selected, True)
        data["detail"] = record
        compact = _Compact()
        data["sourceFacts"] = [compact.fields(fact, ("file", "sourceHash", "mtimeNs", "stableRead"), "sourceFacts", 1024) for fact in result.get("sourceFacts", []) if fact.get("file") == selected.get("file")][:1]
        data["pagination"].update(offset=0, total=1, emitted=1)
        _record_omissions(data, data["detail"])
        if not _fits(data):
            data["detail"] = _placeholder(selected, section)
            data["projection"]["truncated"] = True
            if selected.get("id") not in data["projection"]["recordOmissions"]:
                data["projection"]["recordOmissions"].append(selected.get("id"))
        if not _fits(data):
            _shrink_header(data)
        return data
    matches = [row for row in rows if _matches(row, query, section, tests)]
    data["pagination"]["total"] = len(matches)
    offset = query["offset"]
    if not _fits(data):
        _shrink_header(data)
    for row in matches[offset:offset + query["limit"]]:
        previous_omissions = list(data["projection"]["recordOmissions"])
        previous_truncated = data["projection"]["truncated"]
        record = _condition(row, tests) if section == "conditions" else _test(row)
        data["records"].append(record)
        _record_omissions(data, record)
        _cursor(data, offset, len(matches))
        if not _fits(data):
            data["records"].pop()
            data["projection"]["recordOmissions"] = previous_omissions
            data["projection"]["truncated"] = previous_truncated
            data["projection"]["pageLimitedByBytes"] = True
            data["projection"]["truncated"] = True
            _cursor(data, offset, len(matches))
            if data["records"]:
                break
            data["records"].append(_placeholder(row, section))
            data["projection"]["truncated"] = True
            data["projection"]["recordOmissions"].append(row.get("id"))
            _cursor(data, offset, len(matches))
            if not _fits(data):
                _shrink_header(data)
    return data


def _cursor(data: dict, offset: int, total: int) -> None:
    """Обновляет курсор до измерения размера: числа и omissions также занимают MCP bytes."""
    emitted = len(data["records"])
    next_offset = offset + emitted
    data["pagination"].update(emitted=emitted, nextOffset=next_offset if emitted and next_offset < total else None)


def _record_omissions(data: dict, record: dict) -> None:
    """Отмечает неполные records отдельно от truncation исходного анализа."""
    if record.get("fieldsOmitted"):
        data["projection"]["truncated"] = True
        identifier = record.get("id")
        if identifier not in data["projection"]["recordOmissions"]:
            data["projection"]["recordOmissions"].append(identifier)


def _shrink_header(data: dict) -> None:
    """Ограничивает metadata prefix, сохраняя summary, analysisId и точную pagination; fail closed при невозможном ID."""
    data["projection"]["truncated"] = True
    for field in ("warnings", "reports", "limits", "query", "sourceFacts", "coverageUrl"):
        if _fits(data):
            return
        if field in data:
            data[field] = None if field == "coverageUrl" else [] if isinstance(data[field], list) else {}
            data["projection"]["fieldsOmitted"].append(field)
    if not _fits(data):
        raise ValueError("Метаданные превышают MCP-бюджет; анализ недоступен в компактном виде.")
