"""Безопасные Cobertura counters; агрегированный отчёт не назначает результат AST-arm или тесту."""
from collections import defaultdict
import hashlib
from decimal import Decimal
import time
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

from .source_viewer import SourceError, _source_path, _read_regular_file
from .test_coverage_sources import stable_id


def report_filename(value: str, candidates: set[str], root) -> str | None:
    """Сопоставляет exact repository-relative путь; absolute допустим только под root, без basename guessing."""
    value = value.replace("\\", "/")
    if "\x00" in value or ".." in PurePosixPath(value).parts or re.match(r"^[A-Za-z]+://", value):
        return None
    if value.startswith("/"):
        prefix = root.resolve().as_posix().rstrip("/") + "/"
        if not value.startswith(prefix):
            return None
        value = value[len(prefix):]
    value = str(PurePosixPath(value))
    return value if value in candidates else None


def nonnegative(value: str | None) -> int | None:
    """Принимает ограниченный целый счётчик, а не процент или знак/float."""
    if value is None or not re.fullmatch(r"\d{1,15}", value):
        return None
    return int(value)


def line_counter(element) -> dict | None:
    """Берёт covered/valid из condition-coverage; номера child conditions — IL-offset, не AST-arm."""
    number, hits = nonnegative(element.get("number")), nonnegative(element.get("hits"))
    if not number or hits is None:
        return None
    covered, valid = None, None
    if element.get("branch", "false").casefold() == "true":
        value = element.get("condition-coverage", "")
        match = re.fullmatch(r"\s*\d+(?:\.\d+)?%\s*\((\d{1,15})/(\d{1,15})\)\s*", value)
        if match:
            covered, valid = int(match[1]), int(match[2])
            if not valid or covered > valid:
                covered, valid = None, None
            else:
                percentage = Decimal(value.strip().split("%", 1)[0])
                # Coverlet writes hundredths; other producers round to whole percents.
                tolerance = Decimal("0.5") if "." not in str(percentage) else Decimal("0.01")
                if abs(percentage - Decimal(covered) * 100 / Decimal(valid)) > tolerance:
                    covered, valid = None, None
            if covered and hits == 0:
                covered, valid = None, None
    return {"lineHits": hits, "branchesCovered": covered, "branchesValid": valid}


def load_reports(root, paths: list[str], candidates: set[str], limits: dict) -> dict:
    """Один bounded safe read на explicit/discovered report; DTD/entities отвергаются до XML parsing."""
    reports, counters, bytes_read = [], defaultdict(list), 0
    truncated = len(paths) > limits["maxReports"]
    seen = set()
    for filename in paths[:limits["maxReports"]]:
        fact = {"id": stable_id("coverage-report", filename), "file": filename, "format": "cobertura",
                "status": "rejected", "fileCount": 0, "lineCount": 0, "mtimeNs": None, "runTimestampNs": None, "sourceHash": None, "warnings": []}
        reports.append(fact)
        try:
            canonical_root, relative = _source_path(root, {"file": filename})
            key = relative.as_posix()
            fact["file"] = key
            if key in seen:
                reports.pop()
                continue
            seen.add(key)
            if bytes_read + limits["maxReportBytes"] > limits["maxTotalReportBytes"]:
                fact["warnings"].append("Исчерпан общий лимит байтов отчётов.")
                truncated = True
                continue
            before = (canonical_root / relative).stat()
            payload = _read_regular_file(canonical_root, relative, max_bytes=limits["maxReportBytes"])
            bytes_read += len(payload)
            after = (canonical_root / relative).stat()
            fact.update(mtimeNs=after.st_mtime_ns, sourceHash=hashlib.sha256(payload).hexdigest())
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                fact["warnings"].append("Отчёт изменился при чтении.")
                continue
            if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper() or b"\x00" in payload:
                fact["warnings"].append("DTD, entities и не-UTF8 XML не поддерживаются.")
                continue
            tree = ET.fromstring(payload.decode("utf-8-sig"))
            if tree.tag != "coverage":
                fact["warnings"].append("Корневой элемент не является Cobertura coverage.")
                continue
            timestamp = nonnegative(tree.get("timestamp"))
            invalid_timestamp = tree.get("timestamp") is not None and (not timestamp or timestamp > int(time.time()) + 86400)
            if timestamp and not invalid_timestamp:
                fact["runTimestampNs"] = timestamp * 1000000000
            elif invalid_timestamp:
                fact["warnings"].append("Некорректный timestamp запуска; свежесть не доказана.")
            fact["timestampInvalid"] = invalid_timestamp
            count = sum(1 for _ in tree.iter())
            if count > limits["maxReportElements"]:
                fact["warnings"].append("Исчерпан лимит XML elements.")
                truncated = True
                continue
            rows, mapped_files, invalid, unresolved = defaultdict(list), set(), False, False
            for klass in tree.iter("class"):
                file = report_filename(klass.get("filename", ""), candidates, root)
                if file is None:
                    unresolved = True
                    continue
                mapped_files.add(file)
                for element in klass.iter("line"):
                    counter = line_counter(element)
                    if counter is None:
                        invalid = True
                        number = nonnegative(element.get("number"))
                        if number:
                            rows[(file, number)].append({"lineHits": None, "branchesCovered": None, "branchesValid": None, "invalid": True})
                        continue
                    if element.get("branch", "false").casefold() == "true" and counter["branchesValid"] is None:
                        invalid = True
                    number = int(element.get("number"))
                    rows[(file, number)].append(counter)
            fact["status"] = "partial" if invalid or unresolved else "parsed"
            if invalid:
                fact["warnings"].append("Некоторые line counters некорректны.")
            if unresolved:
                fact["warnings"].append("Некоторые source filenames не сопоставлены с файлами repository; внешние пути не читаются.")
            fact["fileCount"], fact["lineCount"] = len(mapped_files), len(rows)
            for key, values in rows.items():
                # Coverlet repeats method/class lines: identical rows are one observation.
                first = values[0]
                conflict = any(value.get("invalid") or value != first for value in values)
                counters[key].append(dict(first, reportPath=fact["file"], reportMtimeNs=fact["mtimeNs"], runTimestampNs=fact["runTimestampNs"], timestampInvalid=invalid_timestamp, conflict=conflict))
        except (SourceError, OSError, UnicodeError, ET.ParseError, ValueError) as error:
            # Reserve the failed read too, so repeated oversized files cannot exceed the advertised work budget.
            bytes_read += limits["maxReportBytes"]
            fact["warnings"].append(f"Отчёт не прочитан ({getattr(error, 'status', type(error).__name__)}).")
    return {"reports": reports, "counters": counters, "truncated": truncated, "bytes": bytes_read}


def merged_runtime(observations: list[dict], file_fact: dict | None, *, branches=False) -> dict:
    """Не объединяет конфликтующие hits/arms суммой или максимумом; mtime — только проверка совместимости."""
    runtime = {"status": "unknown", "lineHits": None, "branchesCovered": None, "branchesValid": None,
               "reportPaths": sorted({row["reportPath"] for row in observations}), "freshness": "unknown",
               "reason": "Отчёт не содержит сопоставленную строку."}
    if not observations:
        return runtime
    if not file_fact or not file_fact["stableRead"]:
        runtime["reason"] = "Нет стабильного исходного файла."
        return runtime
    if any(row.get("timestampInvalid") for row in observations):
        runtime["reason"] = "Timestamp запуска некорректен; свежесть счётчиков неизвестна."
        return runtime
    if any(file_fact["mtimeNs"] > (row.get("runTimestampNs") or row["reportMtimeNs"]) for row in observations):
        runtime.update(reason="Исходник новее отчёта; счётчики могут быть устаревшими.", freshness="stale")
        return runtime
    runtime["freshness"] = "timestamp-compatible-not-source-hash-proof" if all(row.get("runTimestampNs") for row in observations) else "mtime-compatible-not-source-hash-proof"
    fields = ("lineHits", "branchesCovered", "branchesValid")
    first = observations[0]
    if any(row["conflict"] or any(row[field] != first[field] for field in fields) for row in observations):
        runtime["reason"] = "Счётчики отчётов/повторных строк конфликтуют; они не суммируются."
        return runtime
    runtime.update({field: first[field] for field in fields})
    if branches:
        covered, valid = first["branchesCovered"], first["branchesValid"]
        if valid is None:
            runtime["reason"] = "Line hits не доказывают покрытие переходов; branch counters отсутствуют."
            return runtime
        runtime["status"] = "uncovered" if covered == 0 else "covered" if covered == valid else "partial"
        runtime["reason"] = "Счётчики переходов сопоставленной строки; отдельные AST-цели не назначены."
    else:
        runtime["status"] = "covered" if first["lineHits"] > 0 else "uncovered"
        runtime["reason"] = "Счётчик выполнения сопоставленной строки."
    return runtime


def apply_runtime(methods: list[dict], conditions: list[dict], file_facts: dict, report_data: dict) -> None:
    """Заполняет condition-line aggregates и отдельный method reported-lines summary; tests/objectives не меняет."""
    counters = report_data["counters"]
    same_line = defaultdict(int)
    for condition in conditions:
        same_line[(condition["file"], condition["line"])] += 1
    for condition in conditions:
        key = (condition["file"], condition["line"])
        runtime = merged_runtime(counters.get(key, []), file_facts.get(condition["file"]), branches=True)
        if condition["sourceStatus"] != "parsed" or condition["runtimeAmbiguous"] or same_line[key] > 1:
            runtime["status"] = "unknown"
            runtime["reason"] = "Ошибки разбора, составное/многострочное условие или несколько условий на строке не дают однозначного соответствия переходам отчёта."
        condition["runtime"] = runtime
    by_file = defaultdict(list)
    for (file, number), rows in counters.items():
        by_file[file].append((number, rows))
    for method in methods:
        rows = [(number, values) for number, values in by_file.get(method["file"], []) if method["line"] <= number <= method["endLine"]]
        statuses, paths = [], set()
        for _, values in rows:
            runtime = merged_runtime(values, file_facts.get(method["file"]))
            statuses.append(runtime["status"])
            paths.update(runtime["reportPaths"])
        status = "unknown"
        if statuses and method["sourceStatus"] == "parsed" and "unknown" not in statuses:
            status = "covered" if all(value == "covered" for value in statuses) else "uncovered" if all(value == "uncovered" for value in statuses) else "partial"
        method["runtime"] = {"status": status, "basis": "reported-lines-in-source-range", "reportedLines": len(rows),
            "reportPaths": sorted(paths), "reason": "Только строки, присутствующие в отчёте; полнота метода и отдельных тестов не доказана."}
