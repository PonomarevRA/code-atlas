"""Планирование изменений: черновик или явные диапазоны предположений, без записи кода."""

from copy import deepcopy
import hashlib
from pathlib import Path

from .code_health_comparison import summarize
from .code_health_policy import finding, policy, structural_components, weighted_score
from .code_health_records import HealthError
from .code_health_service import relative_file
from .source_viewer import _read_regular_file, SourceError


def score_range(components: dict, documentation_status: str | None = None, *, weights: dict | None = None) -> list[float]:
    """Неизвестная обязательная часть расширяет диапазон, а не получает хороший балл."""
    low, high, active = {}, {}, {}
    for name, weight in (weights or policy()['weights']).items():
        if name == 'documentation' and documentation_status == 'not-applicable':
            continue
        value = components.get(name)
        interval = value if isinstance(value, list) else [value, value] if value is not None else [0, 100]
        low[name], high[name], active[name] = interval[0], interval[1], weight
    return [weighted_score(low, active), weighted_score(high, active)]


def _scenario(node: dict, projection) -> dict:
    """Изменяет копию исходных метрик лишь как пользовательские допущения."""
    original_metrics = node.get("complexity") or {}
    original_dependencies = node.get("health", {}).get("dependencies") or {}
    outcomes = []
    transparency = []
    assumptions = []
    for extreme in ("max", "min"):
        metrics = deepcopy(original_metrics)
        dependencies = deepcopy(original_dependencies)
        for key, interval in projection.metrics.items():
            target = dependencies if key in {"callTargets", "writes"} else metrics
            target[key] = getattr(interval, extreme)
        required = {"maxNestingDepth", "branchCount", "loopCount", "statementCount"}
        if "branchCount" in metrics and "branchingCount" not in metrics:
            metrics["branchingCount"] = metrics["branchCount"]
        if all(metrics.get(key) is not None for key in required) and (original_metrics.get("status") == "complete" or required <= projection.metrics.keys()):
            metrics["status"] = "complete"
        if {"callTargets", "writes"} <= dependencies.keys() and original_dependencies.get("status") == "complete":
            dependencies["status"] = "complete"
        parts = structural_components(metrics, dependencies)
        parts['organization'] = node.get('health', {}).get('organizationScore')
        if projection.documentationScore:
            parts["documentation"] = projection.documentationScore.min if extreme == "max" else projection.documentationScore.max
        else:
            parts["documentation"] = node.get("documentationHealth", {}).get("score")
        outcomes.append(score_range(parts, node.get("documentationHealth", {}).get("status") if projection.documentationScore is None else None)[0 if extreme == "max" else 1])
        transparency.append(score_range(parts, weights=policy()['transparencyWeights'])[0 if extreme == 'max' else 1])
    for key, interval in projection.metrics.items():
        assumptions.append({"metric": key, "range": [interval.min, interval.max]})
    if projection.documentationScore:
        assumptions.append({"metric": "documentationScore", "range": [projection.documentationScore.min, projection.documentationScore.max]})
    return {"identity": node.get("healthIdentity", node["id"]), "nodeId": node["id"], "label": node.get("label"),
            "file": node.get("file"), "beforeScore": node.get("health", {}).get("score"),
            "scoreRange": sorted(outcomes), 'transparencyRange': sorted(transparency),
            'organizationScore': node.get('health', {}).get('organizationScore'),
            "basis": "scenario-assumptions", "reason": projection.reason,
            "assumptions": assumptions, "unchangedMetrics": "Assumed unchanged unless explicitly projected",
            "semanticReview": "not-performed", "assessmentKind": "estimated"}


def plan_changes(service, project_id: str, query) -> dict:
    """Сохраняет small forecast с baseline ID; предлагаемые тексты не попадают на диск."""
    baseline = service.records.read(project_id, query.baselineAssessmentId)
    if baseline is None:
        return {"status": "unavailable", "verdict": "UNKNOWN", "reason": "Baseline assessment absent or expired"}
    from .code_health import ANALYZER_VERSION
    if baseline.get("policyVersion") != policy()["version"] or baseline.get("analyzerVersion") != ANALYZER_VERSION:
        return {"status": "unavailable", "verdict": "UNKNOWN", "reason": "Baseline policy differs"}
    with service.lock:
        root, graph = service.store.health_snapshot(project_id, baseline.get("snapshotId"),
                                                   files=baseline["files"], limit=120)
        freshness = {}
        for filename, expected in baseline.get("sourceHashes", {}).items():
            try:
                content = _read_regular_file(root.resolve(), Path(relative_file(filename)))
                freshness[filename] = "current" if hashlib.sha256(content).hexdigest() == expected else "changed"
            except SourceError:
                freshness[filename] = "unavailable"
        projections = []
        projected_summary = None
        assumptions = []
        if query.drafts:
            names = [relative_file(item.file) for item in query.drafts]
            if len(set(names)) != len(names):
                raise HealthError("Duplicate draft file")
            contents = {name: draft.content.encode("utf-8") for name, draft in zip(names, query.drafts)}
            # Folder changes affect unchanged files too. Reuse only baseline
            # sources whose hashes still match, and parse the combined scope once.
            complete_contents = dict(contents)
            for filename in baseline['files']:
                if filename not in complete_contents and freshness.get(filename) == 'current':
                    complete_contents[filename] = _read_regular_file(root.resolve(), Path(relative_file(filename)))
            proposed = service.evaluate_contents(graph, complete_contents, locale=query.locale, source_kind='draft', max_methods=120)
            missing_context = set(baseline['files']) - set(complete_contents)
            if missing_context:
                proposed['status'] = 'partial'
                proposed['nodes'].extend(deepcopy(node) for node in baseline['nodes'] if node.get('file') in missing_context)
                for node in proposed['nodes']:
                    health = node['health']
                    health['components']['organization'] = None
                    health.update(organization=None, organizationScore=None, transparencyScore=None, score=None, status='partial')
            if len(proposed['nodes']) > 120:
                proposed['truncated'] = True
                proposed['nodes'] = proposed['nodes'][:120]
            for node in proposed['nodes']:
                health = node.get('health', {})
                projections.append({'identity': node.get('healthIdentity', node['id']), 'nodeId': node['id'],
                    'label': node.get('label'), 'file': node.get('file'),
                    'scoreRange': score_range(health.get('components', {}), node.get('documentationHealth', {}).get('status')),
                    'transparencyRange': score_range(health.get('components', {}), weights=policy()['transparencyWeights']),
                    'basis': 'draft-analysis' if node.get('file') in contents else 'unchanged-baseline-assumption',
                    'organizationScore': health.get('organizationScore'), 'transparencyScore': health.get('transparencyScore'),
                    'readabilityScore': health.get('readabilityScore'), 'readabilityVersion': (health.get('readability') or {}).get('version'),
                    'analysisStatus': health.get('status'), 'findings': health.get('findings', [])[:8],
                    'sourceHash': node.get('complexity', {}).get('sourceHash'), 'assessmentKind': 'estimated'})
            projected_summary = summarize(proposed['nodes'])
            if not missing_context:
                projected_summary['organization'] = proposed['summary']['organization']
            projected_summary["assessmentKind"] = "draft-analysis"
            assumptions = [{"rule": "unchanged-files", "files": sorted(set(baseline["files"]) - set(contents))},
                           {"rule": "unknown-components-range", "range": [0, 100]},
                           {"rule": "behaviour-not-verified"}]
            if missing_context:
                assumptions.append({"rule": "organization-unavailable-for-changed-baseline-files", "files": sorted(missing_context)})
            truncated = proposed["truncated"] or proposed["status"] != "complete"
            assumptions.append({"rule": "file-analysis", "files": proposed["fileAnalysis"]})
            proposed_ids = {node.get("healthIdentity", node["id"]) for node in proposed["nodes"]}
            assumptions.append({"rule": "removed-or-renamed-declarations", "identities": [node.get("healthIdentity", node["id"]) for node in baseline["nodes"] if node.get("healthIdentity", node["id"]) not in proposed_ids]})
        else:
            index = {}
            for node in baseline["nodes"]:
                identity = node.get("healthIdentity", node["id"])
                if identity in index:
                    raise HealthError("Ambiguous baseline identity")
                index[identity] = node
            used = set()
            for projection in query.projections:
                if projection.identity not in index or projection.identity in used:
                    raise HealthError("Choose a unique identity from the baseline assessment")
                used.add(projection.identity)
                projections.append(_scenario(index[projection.identity], projection))
            assumptions = [{"rule": "agent-supplied-target-ranges"}, {"rule": "unprojected-methods-unchanged"},
                           {'rule': 'file-folder-organization-assumed-unchanged-use-full-drafts-for-structural-moves'},
                           {"rule": "behaviour-not-verified"}]
            truncated = baseline.get("truncated", False)
        result = {"baselineAssessmentId": query.baselineAssessmentId, "policyVersion": policy()["version"],
                  "intent": query.intent, "assessmentKind": "estimated", "basis": "draft-analysis" if query.drafts else "scenario-assumptions",
                  "status": "partial" if baseline.get("status", "complete") != "complete" or truncated or any(p.get("analysisStatus") in {"partial", "unavailable"} for p in projections) else "complete", "projections": projections,
                  "baselineFreshness": freshness, "baselineMeasuredAt": baseline.get("measuredAt"),
                  "baselineSummary": baseline["summary"], "projectedSummary": projected_summary,
                  "assumptions": assumptions, "truncated": truncated,
                  "findings": [finding("forecast-only", severity="info", locale=query.locale)],
                  "behaviourVerification": "not-performed"}
        return service.records.save(project_id, result, kind="plan")
