"""Bind imported failure evidence to bounded, already captured source candidates."""

import json

from .junit import FeedbackError, JUnitReport, identity, safe_path

MAX_SOURCE_FACTS = 20000
MAX_PACKAGE_CASES = 20
MAX_PACKAGE_BYTES = 64000


def _source_candidates(case, facts, snapshot_id):
    """
    Find exact static candidates; never claim a runtime test-to-source binding.
    """
    candidates = []
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("snapshotId") != snapshot_id:
            continue
        file, path_status = safe_path(fact.get("file", "")) if isinstance(fact.get("file", ""), str) else (None, "invalid")
        if path_status != "relative":
            continue
        if case.file is not None:
            line_matches = (type(fact.get("line")) is int and fact["line"] == case.line)
            matches = file == case.file and (line_matches if case.line is not None
                                             else fact.get("name") == case.name)
        else:
            matches = bool(case.class_name) and fact.get("className") == case.class_name and fact.get("name") == case.name
        if matches:
            candidates.append(fact)
    return candidates


def build_feedback_package(report: JUnitReport, snapshot_id: str, source_facts: list[dict],
                           build_id: str | None = None) -> dict:
    """
    Build read-only evidence from an immutable report and trusted captured graph facts.

    `source_facts` must come from the server's pinned snapshot, never HTTP caller
    JSON. No arbitrary path is opened, no diagnostic or example is executed.
    """
    if not isinstance(report, JUnitReport):
        raise FeedbackError("invalid_report", "Expected an immutable imported JUnitReport")
    snapshot_id = identity(snapshot_id, "snapshot_id")
    build_id = identity(build_id, "build_id", optional=True)
    if not isinstance(source_facts, list) or len(source_facts) > MAX_SOURCE_FACTS:
        raise FeedbackError("source_limit", "Captured source scope exceeds limit", 413)
    reasons = []
    if not report.cases:
        reasons.append("no-testcases")
    if report.snapshot_id is None:
        reasons.append("report-snapshot-unknown")
    elif report.snapshot_id != snapshot_id:
        reasons.append("report-snapshot-stale")
    if report.build_id is None or build_id is None:
        reasons.append("build-identity-unknown")
    elif report.build_id != build_id:
        reasons.append("report-build-stale")
    failures = [case for case in report.cases if case.status in {"failure", "error", "unknown"}]
    items = []
    for case in failures[:MAX_PACKAGE_CASES]:
        item = case.to_dict()
        item["sourceRefs"] = []
        item["sourceLinkStatus"] = "unknown"
        item["unknownReasons"] = list(reasons)
        if case.outcome_note:
            item["unknownReasons"].append(case.outcome_note)
        if case.ambiguous:
            item["unknownReasons"].append("ambiguous-test-identity")
        elif case.path_status == "invalid":
            item["unknownReasons"].append("invalid-test-path")
        elif not any(reason in reasons for reason in ("report-snapshot-unknown", "report-snapshot-stale", "report-build-stale")):
            candidates = _source_candidates(case, source_facts, snapshot_id)
            if len(candidates) != 1:
                item["unknownReasons"].append("source-not-unique" if candidates else "source-not-found")
            else:
                fact = candidates[0]
                node_id, source_hash = fact.get("id"), fact.get("sourceHash")
                if not isinstance(node_id, str) or not node_id or len(node_id) > 512:
                    item["unknownReasons"].append("source-identity-unknown")
                elif not isinstance(source_hash, str) or not source_hash or len(source_hash) > 128:
                    item["unknownReasons"].append("source-hash-unknown")
                else:
                    item["sourceRefs"] = [{"nodeId": node_id, "snapshotId": snapshot_id,
                                           "file": safe_path(fact["file"])[0],
                                           "line": fact.get("line") if type(fact.get("line")) is int else None,
                                           "sourceHash": source_hash}]
                    item["sourceLinkStatus"] = "matched-to-captured-source"
        items.append(item)
    result = {"schemaVersion": "atlas-debug-feedback/v1", "reportId": report.report_id,
              "reportHash": report.report_hash, "snapshotId": snapshot_id, "buildId": build_id,
              "reportedSnapshotId": report.snapshot_id, "reportedBuildId": report.build_id,
              "identityProvenance": "caller-supplied-report-binding; static snapshot supplied by host",
              "executionVerified": False, "sourceKind": "imported-junit-xml",
              "resultsPresent": bool(report.cases), "sourceFactsCount": len(source_facts),
              "sourceCoverage": "provided captured scope; not a completeness proof",
              "summary": report.summary(), "items": items,
              "truncated": len(items) < len(failures) or any(item["detailsTruncated"] for item in items),
              "unknownReasons": reasons, "interpretation": "Reported outcomes and static candidates, not runtime binding proof.",
              "diagnosticsPolicy": "Common credentials masked; system-out omitted. Review before sharing; no complete secret-detection guarantee."}
    while len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_PACKAGE_BYTES and items:
        items.pop()
        result["truncated"] = True
    return result
