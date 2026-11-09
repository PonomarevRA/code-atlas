"""Budgeted pages of retained health facts; reading never measures source again."""

import json

PAGE_BUDGET = 60_000
METADATA_BUDGET = 12_000


def _size(value: object) -> int:
    """Count the same UTF-8 JSON representation used by the MCP response budget."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _brief_fields(value: dict, names: tuple) -> dict:
    """Keep only bounded scalar facts; Unicode limits count bytes rather than characters."""
    result = {}
    for name in names:
        fact = value.get(name)
        if isinstance(fact, str):
            # JSON can expand one control byte into six bytes. The
            # retained scalar fields are byte-bounded and fit the smallest page.
            result[name] = fact.encode("utf-8")[:128].decode("utf-8", errors="ignore")
        elif fact is None or isinstance(fact, (int, float, bool)):
            if name in value:
                result[name] = fact
    return result


def _brief_coverage(coverage: dict, dimensions: tuple) -> dict:
    """Preserve bounded status/reason/scope; absent legacy evidence remains absent."""
    result = {}
    for dimension in dimensions:
        fact = coverage.get(dimension)
        if isinstance(fact, dict):
            result[dimension] = _brief_fields(fact, ('status', 'reason', 'scope'))
    return result


def _oversized_item(item: dict) -> dict:
    """Keep an oversized row identifiable without pretending its details were delivered."""
    result = {"itemTruncated": True, "warning": "Full item remains in recordUrl."}
    result.update(_brief_fields(item, ("id", "healthIdentity", "identity", "nodeId", "file", "label", "line", "sourceHash")))
    for key in ("health", "complexity", "documentationHealth"):
        facts = item.get(key)
        if isinstance(facts, dict):
            result[key] = _brief_fields(facts, ("version", "score", "readabilityScore", "organizationScore", "transparencyScore", "status", "policyVersion", "analyzerVersion", "sourceHash"))
    profile = (item.get('health') or {}).get('readability')
    if isinstance(profile, dict) and 'health' in result:
        result['health']['readability'] = _brief_fields(profile, ('version', 'status', 'score', 'calibration', 'humanReview'))
    health = item.get('health') or {}
    if isinstance(health, dict) and 'health' in result:
        if isinstance(health.get('componentCoverage'), dict):
            result['health']['componentCoverage'] = _brief_coverage(health['componentCoverage'],
                ('flow', 'size', 'dependencies', 'documentation', 'organization'))
        organization = health.get('organization')
        if isinstance(organization, dict):
            result['health']['organization'] = _brief_fields(organization, ('version', 'status', 'score', 'file', 'folder'))
            if isinstance(organization.get('coverage'), dict):
                result['health']['organization']['coverage'] = _brief_coverage(organization['coverage'],
                    ('inventory', 'local', 'outgoing', 'incoming', 'cycles', 'fragmentation'))
    return result


def health_record_page(record: dict | None, record_id: str, kind: str,
                       offset: int, limit: int, record_url: str) -> dict:
    """Return a progressing immutable page, keeping source and transport limits distinct.

    A single oversized item becomes an explicitly abbreviated row, so following
    nextOffset cannot loop forever. Counts refer to retained facts, not all code.
    """
    result = {"recordId": record_id, "recordKind": kind, "recordUrl": record_url}
    if record is None:
        return result | {"status": "unavailable", "verdict": "UNKNOWN",
                         "reason": "Record absent or expired", "items": [], "nextOffset": None}
    omitted = []
    for key in ("assessmentId", "planId", "baselineAssessmentId", "status", "assessmentKind",
                "policyVersion", "analyzerVersion", "sourceMode", "snapshotId", "measuredAt",
                "createdAt", "graphUpdatedAt", "freshness", "summary", "projectedSummary"):
        if key in record and _size(record[key]) <= 4000 and _size(result) + _size(record[key]) < METADATA_BUDGET - 1024:
            result[key] = record[key]
        elif key in record:
            omitted.append(key)
            if isinstance(record[key], dict):
                abbreviated = {key: _brief_fields(record[key], ("status", "checkedAt", "methodCount", "assessedCount",
                                                               "unknownCount", "meanHealth", "minimumHealth"))}
            else:
                abbreviated = _brief_fields(record, (key,))
            if _size(result | abbreviated) < METADATA_BUDGET - 1024:
                result.update(abbreviated)
    result["sourceScopeTruncated"] = bool(record.get("truncated"))
    if omitted:
        result["metadataTruncated"] = True
        result["abbreviatedMetadata"] = omitted
        result["warning"] = "Metadata abbreviated; full facts remain in recordUrl."
    section = "nodes" if kind == "assessment" else "projections"
    values = record.get(section) or []
    result.update(itemSection=section, totalStoredItems=len(values), offset=offset,
                  items=[], nextOffset=None, responseTruncated=False)
    used = _size(result)
    for item in values[offset:offset + limit]:
        candidate = item
        size = _size(candidate)
        if size > PAGE_BUDGET - METADATA_BUDGET - 1024:
            candidate = _oversized_item(item)
            size = _size(candidate)
        # Reserve punctuation and nextOffset digits independently of item size.
        if used + size + 32 > PAGE_BUDGET:
            result["responseTruncated"] = True
            break
        result["items"].append(candidate)
        used += size + 2
        if candidate is not item:
            result["responseTruncated"] = True
    following = offset + len(result["items"])
    if following < len(values):
        result["nextOffset"] = following
    return result
