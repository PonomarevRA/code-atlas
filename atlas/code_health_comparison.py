"""Сравнение measured оценок не скрывает новые helpers, deferred тела и неизвестность."""

from collections import defaultdict
import math
from .code_health_policy import finding, policy, mean_score


def _policy_mean(values: list, versions: set) -> float | None:
    """Сохраняет историческое округление v1/v2, для новой политики использует точное.

    :param values: Известные баллы только одной совместимой версии.
    :param versions: Захваченные policy IDs; полноту проверяет вызывающий код.
    :returns: Среднее по правилам соответствующей версии либо None для пустой выборки.
    """
    if versions and versions <= {'atlas-health-v1', 'atlas-health-v2'}:
        return round(sum(values) / len(values), 1) if values else None
    return mean_score(values)


def summarize(nodes: list[dict]) -> dict:
    """Все доли имеют знаменатель; meanHealth требует полной выборки.

    knownMeanHealth описывает только известную часть, не итог всего участка.
    Повторные карточки не становятся новыми методами.
    """
    scored, transparency, eligible, documented, unknown_docs = [], [], 0, 0, 0
    statements = branches = deferred = unknown = unknown_inventory = 0
    seen = set()
    component_counts = {key: {'complete': 0, 'unknown': 0, 'total': 0}
                        for key in ('flow', 'size', 'dependencies', 'documentation', 'organization')}
    not_applicable = 0
    health_versions, transparency_versions = set(), set()
    missing_health_version = missing_transparency_version = False
    readable, readability_reviews = [], 0
    readability_versions = set()
    for node in nodes:
        identity = node.get("healthIdentity", node["id"])
        if identity in seen:
            continue
        seen.add(identity)
        health = node.get("health") or {}
        not_applicable += health.get("status") == "not-applicable"
        for key, counts in component_counts.items():
            known = health.get("componentCoverage", {}).get(key, {}).get("status") == "complete"
            counts["complete"] += known
            counts["unknown"] += not known
            counts["total"] += 1
        if health.get('transparencyScore') is not None:
            transparency.append(health['transparencyScore'])
            if health.get('version'):
                transparency_versions.add(health['version'])
            else:
                missing_transparency_version = True
        profile = health.get("readability") or {}
        if profile.get("status") == "complete" and profile.get("version") and profile.get("score") is not None:
            readable.append(profile["score"])
            readability_versions.add(profile["version"])
        readability_reviews += profile.get("verdict") == "REVIEW"
        doc = node.get("documentationHealth") or {}
        metrics = node.get("complexity") or {}
        if health.get("score") is not None:
            scored.append(health["score"])
            if health.get("version"):
                health_versions.add(health["version"])
            else:
                missing_health_version = True
        elif health.get("status") != "not-applicable":
            unknown += 1
        if doc.get("eligible"):
            eligible += 1
            if doc.get("documented") is True:
                documented += 1
            elif doc.get("documented") is None:
                unknown_docs += 1
        unknown_inventory += metrics.get("status") != "complete"
        statements += metrics.get("statementCount") or 0
        branches += metrics.get("branchingCount") or 0
        deferred += node.get("kind") == "deferred"
    compatible_health = len(health_versions) == 1 and not missing_health_version
    compatible_transparency = len(transparency_versions) == 1 and not missing_transparency_version
    transparency_basis = 'captured method policies: ' + (', '.join(sorted(transparency_versions)) or 'unavailable')
    transparency_basis += '; mixed/missing policies have no aggregate; not semantic comprehension'
    distribution = sorted(scored) if compatible_health else []
    p95 = distribution[max(0, math.ceil(len(distribution) * .95) - 1)] if distribution else None
    structural = [n.get('complexity', {}).get('score') for n in nodes if n.get('complexity', {}).get('status') == 'complete']
    structural = [value for value in structural if value is not None]
    from .code_organization import organization_summary
    organization = {n['health']['organization']['file']: n['health']['organization'] for n in nodes
                    if (n.get('health') or {}).get('organization')}
    return {"readability": {"meanScore": round(sum(readable) / len(readable), 1) if readable and len(readability_versions) == 1 else None,
                            "versions": sorted(readability_versions),
                            "assessedMethods": len(readable), "totalMethods": len(seen),
                            "reviewMethods": readability_reviews, "humanReview": "not-performed",
                            "coveragePercent": round(100 * len(readable) / len(seen), 1) if seen else None,
                            "basis": "local syntax heuristic; mean covers measured methods only, not semantic understanding"},
            "organization": organization_summary(organization),
            "transparency": {'score': _policy_mean(transparency, transparency_versions) if len(transparency) == len(seen) and compatible_transparency and seen else None,
                             'assessedMethods': len(transparency), 'totalMethods': len(seen),
                             'policyVersions': sorted(transparency_versions), 'basis': transparency_basis},
            "healthDistribution": {"p05": distribution[int((len(distribution) - 1) * .05)] if distribution else None, "p95": p95},
            "complexityDistribution": {"maximum": max(structural) if structural else None,
                "shareAt10OrMore": round(100 * sum(value >= 10 for value in structural) / len(structural), 1) if structural else None},
            "methodCount": len(seen), "assessedCount": len(scored), "unknownCount": unknown,
            "componentCoverage": component_counts,
            "meanHealth": _policy_mean(scored, health_versions) if scored and compatible_health and len(scored) == len(seen) - not_applicable else None,
            "knownMeanHealth": _policy_mean(scored, health_versions) if scored and compatible_health else None,
            "minimumHealth": min(scored) if scored and compatible_health else None,
            "policyVersions": sorted(health_versions),
            "policyCompatibility": "complete" if compatible_health else "unknown-or-mixed",
            "documentation": {"eligible": eligible, "documented": documented, "unknown": unknown_docs,
                "coveragePercent": round(100 * documented / eligible, 1) if eligible and not unknown_docs else None,
                "knownDocumentedLowerBound": round(100 * documented / eligible, 1) if eligible else None},
            "logicInventory": {"statements": statements, "branches": branches, "deferredBodies": deferred,
                               "unknownDeclarations": unknown_inventory, "status": "partial" if unknown_inventory else "complete",
                               "basis": "static declaration inventory; not executed alternatives"}}


def _index(nodes):
    """Неоднозначные identity исключаются из автоматического сопоставления."""
    groups = defaultdict(list)
    for node in nodes:
        groups[node.get("healthIdentity", node["id"])].append(node)
    return {key: items[0] for key, items in groups.items() if len(items) == 1}, [key for key, items in groups.items() if len(items) > 1]



def compare_readability(before: dict, after: dict) -> dict:
    """Compare only complete R profiles under the same token/syntax policy; missing history stays unknown."""
    first = (before.get('health') or {}).get('readability') or {}
    second = (after.get('health') or {}).get('readability') or {}
    comparable = (first.get('version') is not None and first.get('version') == second.get('version') and
                  first.get('status') == second.get('status') == 'complete' and
                  first.get('score') is not None and second.get('score') is not None)
    return {'before': first.get('score'), 'after': second.get('score'),
            'delta': round(second['score'] - first['score'], 1) if comparable else None,
            'status': 'complete' if comparable else 'unavailable', 'humanReview': 'not-performed'}


def compare_assessments(before: dict | None, after: dict | None, *, identity_map: dict | None = None,
                        plan: dict | None = None, locale: str = "en") -> dict:
    """Сравнивает одну политику и scope; incompatible/expired сведения дают UNKNOWN."""
    if before is None or after is None:
        return {"status": "unavailable", "verdict": "UNKNOWN", "reason": "Assessment absent or expired"}
    if before.get("policyVersion") != after.get("policyVersion") or before.get("analyzerVersion") != after.get("analyzerVersion"):
        return {"status": "unavailable", "verdict": "UNKNOWN", "reason": "Policy/analyzer versions differ"}
    different_scope = before.get("files") != after.get("files")
    different_basis = before.get("sourceMode") != after.get("sourceMode")
    left, ambiguous_left = _index(before["nodes"])
    right, ambiguous_right = _index(after["nodes"])
    mapping = identity_map or {}
    if set(mapping) - set(left) or set(mapping.values()) - set(right):
        return {"status": "unavailable", "verdict": "UNKNOWN", "reason": "Mapping identities are absent or ambiguous"}
    changes, findings, consumed = [], [], set()
    for identity, node in left.items():
        target = mapping.get(identity, identity)
        current = right.get(target)
        if current is None:
            findings.append(finding("removed-method", file=node.get("file", ""), line=node.get("line"), severity="info", locale=locale))
            changes.append({"identity": identity, "kind": "removed-or-renamed", "before": node.get("health", {}).get("score"), "after": None})
            continue
        if target in consumed:
            return {"status": "unavailable", "verdict": "UNKNOWN", "reason": "Mapping is not one-to-one"}
        consumed.add(target)
        first, second = node.get("health", {}).get("score"), current.get("health", {}).get("score")
        delta = round(second - first, 1) if first is not None and second is not None else None
        changes.append({"identity": identity, "afterIdentity": target, "nodeId": current["id"], "kind": "changed" if node.get("complexity", {}).get("sourceHash") != current.get("complexity", {}).get("sourceHash") else "unchanged",
                        'contextChanged': node.get('health', {}).get('organization') != current.get('health', {}).get('organization'),
                        "before": first, "after": second, "delta": delta,
                        "readability": compare_readability(node, current),
                        "componentCoverage": {"before": node.get("health", {}).get("componentCoverage"),
                                              "after": current.get("health", {}).get("componentCoverage")},
                        "documentationDelta": round(current["documentationHealth"]["score"] - node["documentationHealth"]["score"], 1)
                            if node.get("documentationHealth", {}).get("score") is not None and current.get("documentationHealth", {}).get("score") is not None else None,
                        **{key + 'Delta': round(current['health'][key + 'Score'] - node['health'][key + 'Score'], 1)
                           if current.get('health', {}).get(key + 'Score') is not None and node.get('health', {}).get(key + 'Score') is not None else None
                           for key in ('organization', 'transparency')}})
        if delta is not None and delta < -5:
            findings.append(finding("regression", file=current.get("file", ""), line=current.get("line"), evidence={"delta": delta}, locale=locale))
    for identity, node in right.items():
        if identity not in consumed:
            changes.append({"identity": identity, "kind": "added", "nodeId": node["id"], "before": None, "after": node.get("health", {}).get("score")})
    first_summary, second_summary = summarize(before["nodes"]), summarize(after["nodes"])
    # Retain a complete assessment's context instead of rebuilding it from a
    # method page, which could silently exclude empty/unsupported source files.
    for summary, assessment in ((first_summary, before), (second_summary, after)):
        if assessment.get('summary', {}).get('organization'):
            summary['organization'] = assessment['summary']['organization']
    growth = second_summary["methodCount"] - first_summary["methodCount"]
    if growth >= policy()["review"]["fragmentationMethods"] and second_summary["logicInventory"]["statements"] >= first_summary["logicInventory"]["statements"] * 0.8:
        findings.append(finding("fragmentation", evidence={"addedMethods": growth}, locale=locale))
    warnings = findings + [item for n in after["nodes"] for item in n.get("health", {}).get("findings", []) if item["severity"] in {"warning", "critical"}]
    unknown = before.get("status", "complete") != "complete" or after.get("status", "complete") != "complete" or any(n.get("health", {}).get("status") not in {"complete", "not-applicable"} for n in before["nodes"]) or different_scope or different_basis or before.get("truncated") or after.get("truncated") or ambiguous_left or ambiguous_right or any(n.get("health", {}).get("status") not in {"complete", "not-applicable"} for n in after["nodes"]) or any(c.get("delta") is None and c["kind"] in {"changed", "removed-or-renamed"} for c in changes)
    result = {"status": "partial" if unknown else "complete", "verdict": "REVIEW" if any(f["severity"] in {"warning", "critical"} for f in warnings) else "UNKNOWN" if unknown else "PASS",
              "beforeAssessmentId": before.get("assessmentId"), "afterAssessmentId": after.get("assessmentId"),
              "changes": changes, "findings": warnings, "beforeSummary": first_summary, "afterSummary": second_summary,
              "ambiguousIdentities": ambiguous_left + ambiguous_right, "aggregateScopesComparable": not different_scope and not different_basis,
              "sourceEvidence": {"before": {k: before.get(k) for k in ("sourceMode", "snapshotId", "measuredAt", "graphUpdatedAt", "freshness")},
                                 "after": {k: after.get(k) for k in ("sourceMode", "snapshotId", "measuredAt", "graphUpdatedAt", "freshness")}}, "behaviourVerification": "not-performed"}
    if plan:
        expected = {p["identity"]: p for p in plan.get("projections", [])}
        result["planComparison"] = []
        for change in changes:
            identity = change.get("afterIdentity", change["identity"])
            interval = expected.get(identity, expected.get(change["identity"], {})).get("scoreRange")
            if interval is None:
                continue
            actual = change.get("after")
            within = interval[0] <= actual <= interval[1] if interval and actual is not None else None
            result["planComparison"].append({"identity": identity, "expected": interval, "actual": actual, "withinRange": within})
    return result
