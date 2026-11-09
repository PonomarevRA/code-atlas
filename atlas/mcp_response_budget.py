"""Exact UTF-8 JSON budgets for MCP list responses without repeated whole dumps."""

import json
from collections.abc import Iterable

MCP_RESPONSE_BYTES = 64_000


def json_bytes(value: object) -> int:
    """Measure the existing MCP JSON representation, including Unicode byte width."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _flow_summary(flow: dict) -> dict:
    """Preserve the language/provider coverage boundary when detailed execution cards are omitted."""
    result = {key: flow[key] for key in ("version", "contractVersion", "language", "status", "truncated", "scope")
              if key in flow and isinstance(flow[key], (str, bool, int, type(None)))}
    unsupported = flow.get("unsupported")
    if isinstance(unsupported, list):
        result["unsupported"] = [str(item)[:400] for item in unsupported[:8]]
        result["unsupportedTruncated"] = len(unsupported) > 8 or any(len(str(item)) > 400 for item in unsupported[:8])
    result["stepsOmitted"] = True
    return result


def compact_description(result: dict, snapshot_id: str, node_id: str, comment_fields: Iterable[str]) -> dict:
    """
    Keep bounded health, uncertainty and provenance when a description exceeds its context budget.

    Args:
        result: Описание HTTP API с безопасными сводными счётчиками.
        snapshot_id: Закреплённая ревизия для продолжения чтения.
        node_id: Полный raw-ID; сокращённый ID не создаётся.
        comment_fields: Известные поля захваченных комментариев.
    Returns:
        DTO до 64000 UTF-8 байт с сохранённой границей полноты.
    Raises:
        ValueError: Обязательные идентификаторы не помещаются в бюджет.
    """
    fallback = {"truncated": True, "snapshotId": snapshot_id, "nodeId": node_id,
                **{key: result[key] for key in ('analysisStatus', 'errorCount', 'coverage', 'analysisIssue', 'failedFileCount', 'excludedDataCount', 'processedFileCount', 'sourceFileCount') if key in result},
                "factualSummary": str(result.get("factualSummary", ""))[:4000],
                "warning": "Подробное описание превышает MCP-бюджет. Используйте get_node(snapshotId,nodeId) и get_neighbors(snapshotId,nodeId); полное описание доступно в Atlas."}
    # Bound each preserved section independently: source comments and findings can
    # themselves be the reason the full description exceeded the response budget.
    for key in ("health", "complexity", "sourceProvenance", "sourceKind", "sourceStatus", "file", "sourceHash", "analyzerVersion",
                "language", "flowProvider", "methodFlow", "truncation", "astExploration", "semanticFacts", "semanticEngines",
                "readabilityRecommendations", "infrastructure", "infrastructureCoverage", "analysisFacts", "analysisContext", "sectionExploration",
                "testFacts", "testContainerFacts", "codeProjectFacts", "memberFacts", "fieldFacts", "dataFacts", "entrypointFacts", "htmlFacts", *comment_fields):
        if key not in result:
            continue
        value = result[key]
        if key == "methodFlow" and isinstance(value, dict):
            value = _flow_summary(value)
        if json_bytes(value) <= 6000:
            fallback[key] = value
        elif isinstance(value, dict):
            summary = {}
            for name, item in value.items():
                if isinstance(item, (str, int, float, bool, type(None))):
                    summary[name] = item[:512] if isinstance(item, str) else item
                elif name in {"components", "dependencies", "readability"} and isinstance(item, dict):
                    summary[name] = {field: fact for field, fact in item.items()
                                     if isinstance(fact, (int, float, bool, type(None))) or isinstance(fact, str) and len(fact) <= 128}
                if json_bytes(summary) > 3000:
                    summary.pop(name, None)
                    break
            summary["detailsOmitted"] = True
            fallback[key] = summary
        elif isinstance(value, str):
            fallback[key] = value[:1000]
        else:
            fallback[key] = {"detailsOmitted": True}
        if json_bytes(fallback) > MCP_RESPONSE_BYTES - 512:
            fallback.pop(key, None)
            fallback["detailsOmitted"] = True
    facts = result.get("attributeFacts") or {}
    captured = result.get("capturedAttribute") or {}
    provenance = facts if facts.get("sourceKind") == "snapshot-captured-source" else captured
    available = provenance.get("sourceKind") == "snapshot-captured-source"
    fallback["attributeFactsAvailable"] = available
    if available:
        fallback["attributeFactsProvenance"] = {"sourceKind": "snapshot-captured-source"}
        if isinstance(provenance.get("sourceHash"), str):
            fallback["attributeFactsProvenance"]["sourceHash"] = provenance["sourceHash"][:128]
    if json_bytes(fallback) > MCP_RESPONSE_BYTES:
        raise ValueError("Description identifiers exceed the MCP budget")
    return fallback


def bound_list_response(result: dict, keys: Iterable[str], *, flag: str = "truncated",
                        budget: int = MCP_RESPONSE_BYTES) -> dict | None:
    """Keep ordered list prefixes within budget; return None if metadata alone is too large.

    The input and its nested lists remain untouched. Each removed item is encoded once
    after the initial size pass, so large pages do not serialize every remaining row
    again. Arithmetic includes the default JSON comma-space separators.
    """
    size = json_bytes(result)
    if size <= budget:
        return result
    bounded = dict(result)
    if flag in bounded:
        size += json_bytes(True) - json_bytes(bounded[flag])
    else:
        size += json_bytes({flag: True}) - 2 + (2 if bounded else 0)
    bounded[flag] = True
    for key in keys:
        values = bounded.get(key)
        if not isinstance(values, list):
            continue
        count = len(values)
        while count and size > budget:
            size -= json_bytes(values[count - 1]) + (2 if count > 1 else 0)
            count -= 1
        if count != len(values):
            bounded[key] = values[:count]
        if size <= budget:
            return bounded
    return bounded if size <= budget else None
