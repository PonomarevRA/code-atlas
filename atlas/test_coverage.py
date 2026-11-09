"""Offline read-only inventory, static links and reported counters; never runs tests."""
from collections import Counter, defaultdict, deque
import json
import os
from pathlib import Path
from .test_coverage_sources import SourceBudget, scan_csharp, stable_id
from .test_coverage_gherkin import scan_gherkin, bind_steps
from .test_coverage_reports import load_reports, apply_runtime

LIMITS = {"maxSourceFiles": 500, "maxSourceBytes": 32 * 1024 * 1024, "maxMethods": 3000,
    "maxConditions": 10000, "maxTests": 2000, "maxFeatureFiles": 100, "maxAstNodes": 1500000,
    "maxChainNodes": 600, "maxChainDepth": 30, "maxGraphNodes": 50000, "maxGraphEdges": 200000,
    "maxGraphWalkSteps": 1000000, "maxTestLinks": 2, "maxProofPathNodes": 2, "maxTestTargets": 8, "maxMethodTests": 8,
    "maxReports": 8, "maxReportBytes": 8 * 1024 * 1024,
    "maxTotalReportBytes": 32 * 1024 * 1024, "maxReportElements": 250000,
    "maxCatalogueEntries": 25000, "maxStepsPerTest": 120, "maxExampleRows": 500,
    "maxResponseBytes": 8 * 1024 * 1024}
EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "bin", "obj", ".idea", ".vs", ".atlas", "__pycache__"}


class TestCoverageError(RuntimeError):
    """Transport-neutral validation error; HTTP owner reads status."""
    __test__ = False
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def discover_files(root: Path, limits: dict) -> dict:
    """Bounded no-follow feature/report catalogue; never selects an arbitrary newest report."""
    pending, features, reports, inspected, truncated = [root], [], [], 0, False
    while pending:
        directory = pending.pop()
        entries = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    inspected += 1
                    if inspected > limits["maxCatalogueEntries"]:
                        truncated = True
                        break
                    entries.append(entry)
        except OSError:
            continue
        for entry in sorted(entries, key=lambda value: value.name):
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in EXCLUDED_DIRS:
                    pending.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                relative = Path(entry.path).relative_to(root).as_posix()
                lower = entry.name.casefold()
                if lower.endswith(".feature"):
                    features.append(relative)
                if lower in {"coverage.cobertura.xml", "cobertura.xml", "coverage.xml"} or lower.startswith("coverage") and lower.endswith(".cobertura.xml"):
                    reports.append(relative)
        if truncated:
            break
    return {"features": sorted(features), "reports": sorted(reports), "truncated": truncated}


def graph_index(graph: dict, limits: dict) -> dict:
    """Only original raw IDs/call relations enter paths; attribute edges are never calls."""
    raw_nodes, raw_edges = graph.get("nodes", []), graph.get("edges", [])
    nodes = {node["id"]: node for node in raw_nodes[:limits["maxGraphNodes"]] if isinstance(node, dict) and isinstance(node.get("id"), str)}
    adjacency = defaultdict(list)
    for edge in raw_edges[:limits["maxGraphEdges"]]:
        if not isinstance(edge, dict) or edge.get("relation") not in {"calls", "dispatches_to"}:
            continue
        if edge.get("source") in nodes and edge.get("target") in nodes:
            adjacency[edge["source"]].append(edge)
    for edges in adjacency.values():
        edges.sort(key=lambda edge: (edge["target"], edge["relation"], str(edge.get("confidence") or "")))
    return {"nodes": nodes, "adjacency": adjacency, "truncated": len(raw_nodes) > len(nodes) or len(raw_edges) > limits["maxGraphEdges"]}


def walk_calls(start: str, index: dict, limits: dict, work: dict) -> tuple[dict, bool]:
    """Finite BFS keeps one deterministic shortest source path per raw target."""
    paths = {start: {"path": [start], "relations": [], "confidence": "source", "edgeEvidence": []}}
    pending, truncated = deque([(start, 0)]), False
    while pending:
        current, depth = pending.popleft()
        for edge in index["adjacency"].get(current, []):
            work["steps"] += 1
            if work["steps"] > limits["maxGraphWalkSteps"]:
                return paths, True
            target = edge["target"]
            if target in paths:
                continue
            if depth >= limits["maxChainDepth"] or len(paths) >= limits["maxChainNodes"]:
                truncated = True
                continue
            previous = paths[current]
            candidate = edge["relation"] == "dispatches_to" or str(edge.get("confidence") or "").upper() in {"INFERRED", "CANDIDATE", "LOW"}
            evidence = {key: edge[key] for key in ("relation", "confidence", "_origin", "source_file", "source_location", "evidence") if key in edge}
            paths[target] = {"path": previous["path"] + [target], "relations": previous["relations"] + [edge["relation"]],
                "confidence": "candidate" if candidate or previous["confidence"] == "candidate" else "source", "edgeEvidence": previous["edgeEvidence"] + [evidence]}
            pending.append((target, depth + 1))
    return paths, truncated


def static_links(tests: list[dict], conditions: list[dict], methods: list[dict], index: dict, limits: dict, work: dict) -> bool:
    """Graph reachability is a static link, never per-test coverage; Gherkin starts at lexical bindings."""
    methods_by_owner, conditions_by_owner = defaultdict(list), defaultdict(list)
    for method in methods:
        if method["ownerNodeId"]:
            methods_by_owner[method["ownerNodeId"]].append(method)
    for condition in conditions:
        if condition["ownerNodeId"]:
            conditions_by_owner[condition["ownerNodeId"]].append(condition)
        condition["testLinksTotal"], condition["testLinksTruncated"] = 0, False
        condition["testKinds"] = {"unit": 0, "gherkin": 0}
    truncated = False
    for test in tests:
        starts = [test["nodeId"]] if test["nodeId"] else []
        bindings = {}
        for step in test.get("steps", []):
            for binding in step["bindings"]:
                bindings[binding["nodeId"]] = binding
        starts.extend(sorted(bindings))
        targets, linked_targets = set(), set()
        for start in dict.fromkeys(starts):
            paths, incomplete = walk_calls(start, index, limits, work)
            truncated |= incomplete
            for target, proof in paths.items():
                if target not in methods_by_owner:
                    continue
                targets.add(target)
                if target in linked_targets:
                    continue
                linked_targets.add(target)
                for method in methods_by_owner[target]:
                    if test["id"] not in method["testIds"]:
                        method["testIds"].append(test["id"])
                for condition in conditions_by_owner[target]:
                    if any(link["testId"] == test["id"] for link in condition["testLinks"]):
                        continue
                    condition["testLinksTotal"] += 1
                    condition["testKinds"][test["kind"]] += 1
                    replace_index = None
                    if len(condition["testLinks"]) >= limits["maxTestLinks"]:
                        truncated = True
                        condition["testLinksTruncated"] = True
                        kinds = {link["testKind"] for link in condition["testLinks"]}
                        if test["kind"] in kinds:
                            continue
                        # Keep one representative of each available kind, so early unit tests cannot hide Gherkin.
                        replace_index = len(condition["testLinks"]) - 1
                    link = compact_proof(proof, limits)
                    link.update(testId=test["id"], testKind=test["kind"], targetNodeId=target, sourceKind="graph-snapshot")
                    if start in bindings:
                        link.update(confidence="candidate", bindingEvidence=bindings[start], sourceKind="working-tree-and-snapshot")
                    if replace_index is None:
                        condition["testLinks"].append(link)
                    else:
                        condition["testLinks"][replace_index] = link
            if work["steps"] > limits["maxGraphWalkSteps"]:
                break
        test["staticTargetsTotal"] = len(targets)
        test["staticTargetsTruncated"] = len(targets) > limits["maxTestTargets"]
        test["staticTargetIds"] = sorted(targets)[:limits["maxTestTargets"]]
        truncated |= test["staticTargetsTruncated"]
        if work["steps"] > limits["maxGraphWalkSteps"]:
            truncated = True
            break
    for method in methods:
        method["testsTotal"] = len(method["testIds"])
        method["testsTruncated"] = len(method["testIds"]) > limits["maxMethodTests"]
        method["testIds"] = method["testIds"][:limits["maxMethodTests"]]
        truncated |= method["testsTruncated"]
    return truncated


def compact_proof(proof: dict, limits: dict) -> dict:
    """Keeps full short paths; long prefixes/suffixes explicitly omit the middle rather than inventing a direct edge."""
    path = proof["path"]
    omitted = len(path) > limits["maxProofPathNodes"]
    evidence = []
    for edge in proof["edgeEvidence"]:
        if edge.get("relation") != "dispatches_to" and str(edge.get("confidence") or "").upper() not in {"INFERRED", "CANDIDATE", "LOW"}:
            continue
        compact = {key: str(edge[key])[:512] for key in ("relation", "confidence", "_origin", "source_file", "source_location") if key in edge}
        if isinstance(edge.get("evidence"), dict):
            compact["evidence"] = {str(key)[:100]: str(value)[:512] for key, value in list(edge["evidence"].items())[:16]}
        evidence.append(compact)
    return {"path": [] if omitted else list(path), "pathTruncated": omitted, "pathLength": len(path),
        "pathPrefix": path[:1] if omitted else [], "pathSuffix": path[-1:] if omitted else [],
        "relations": list(proof["relations"]), "confidence": proof["confidence"],
        "edgeEvidence": evidence[:2], "edgeEvidenceTruncated": len(evidence) > 2}


def inventory_summary(tests: list[dict], methods: list[dict], conditions: list[dict], namespaces: list[dict], reports: list[dict]) -> dict:
    """Separates source inventory from reported condition-line aggregates."""
    def counts(values, choices):
        """Returns zero entries too, keeping the UI contract stable."""
        counter = Counter(values)
        return {choice: counter[choice] for choice in choices}
    return {"tests": len(tests), "methods": len(methods), "conditions": len(conditions), "namespaces": len(namespaces), "reports": len(reports),
        "testsByKind": counts((test["kind"] for test in tests), ("unit", "gherkin")),
        "testsByDeclaredStatus": counts((test["declaredStatus"] for test in tests), ("enabled", "disabled", "unknown")),
        "conditionsByStatus": counts((condition["runtime"]["status"] for condition in conditions), ("covered", "partial", "uncovered", "unknown"))}


def bound_response(result: dict, limit: int) -> None:
    """Drops tail records in deterministic batches when UTF8 exceeds budget; summary remains pre-omission inventory."""
    result["returned"] = {key: len(result[key]) for key in ("conditions", "tests", "methods", "namespaces")}
    while len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > limit:
        result["status"] = "partial"
        result["truncation"]["response"] = True
        largest = max(("conditions", "tests", "methods", "namespaces"), key=lambda key: len(result[key]))
        if not result[largest]:
            break
        result[largest] = result[largest][:max(0, len(result[largest]) - max(1, len(result[largest]) // 4))]
        result["returned"][largest] = len(result[largest])


def build_test_coverage(root: Path, graph: dict, snapshot_id: str, node_id: str | None = None, report_paths: list[str] | None = None) -> dict:
    """Builds bounded current-source inventory against one immutable graph; invalid inputs raise TestCoverageError."""
    if not isinstance(snapshot_id, str) or not snapshot_id or len(snapshot_id) > 200:
        raise TestCoverageError("snapshotId обязателен.")
    if node_id is not None and (not isinstance(node_id, str) or not node_id or len(node_id) > 1024):
        raise TestCoverageError("Некорректный nodeId.")
    if report_paths is not None and (not isinstance(report_paths, list) or len(report_paths) > LIMITS["maxReports"] or any(not isinstance(path, str) or not path or len(path) > 1024 for path in report_paths)):
        raise TestCoverageError("reportPaths: не более 8 repository-relative путей.")
    limits = dict(LIMITS)
    root = Path(root).resolve()
    if not root.is_dir():
        raise TestCoverageError("Repository недоступен.", 404)
    index = graph_index(graph, limits)
    if node_id is not None and node_id not in index["nodes"]:
        raise TestCoverageError("Узел snapshot не найден.", 404)
    work = {"steps": 0}
    scope_paths, scope_truncated = walk_calls(node_id, index, limits, work) if node_id else ({}, False)
    graph_files = sorted({str(node.get("file") or node.get("source_file") or "") for node in index["nodes"].values() if str(node.get("file") or node.get("source_file") or "").casefold().endswith(".cs")})
    scope_files = {str(index["nodes"][raw].get("file") or index["nodes"][raw].get("source_file") or "") for raw in scope_paths}
    graph_files.sort(key=lambda file: (file not in scope_files, "test" not in file.casefold(), file))
    catalogue = discover_files(root, limits)
    report_data = load_reports(root, report_paths if report_paths else catalogue["reports"], set(graph_files), limits)
    budget = SourceBudget(root, snapshot_id, limits)
    # Reserve feature slots before the C# catalogue consumes the shared 500-file budget.
    for feature in catalogue["features"][:limits["maxFeatureFiles"]]:
        budget.read(feature)
    source = scan_csharp(budget, graph, graph_files, limits)
    gherkin_limits = dict(limits, maxTests=max(0, limits["maxTests"] - len(source["tests"])))
    gherkin = scan_gherkin(budget, catalogue["features"], gherkin_limits)
    bind_steps(gherkin["tests"], source["bindings"])
    methods, conditions = source["methods"], source["conditions"]
    # Count ambiguity before node scope removes sibling methods on the same physical source line.
    all_lines = Counter((condition["file"], condition["line"]) for condition in conditions)
    for condition in conditions:
        condition["runtimeAmbiguous"] |= all_lines[(condition["file"], condition["line"])] > 1
    if node_id:
        methods = [method for method in methods if method["ownerNodeId"] in scope_paths]
        ids = {method["id"] for method in methods}
        conditions = [condition for condition in conditions if condition["methodId"] in ids]
    tests = source["tests"] + gherkin["tests"]
    links_truncated = static_links(tests, conditions, methods, index, limits, work)
    apply_runtime(methods, conditions, source["fileFacts"], report_data)
    namespaces_by_name = {}
    for method in methods:
        namespace = namespaces_by_name.setdefault(method["namespace"], {"id": stable_id("coverage-namespace", method["namespace"]), "name": method["namespace"], "testIds": [], "methodIds": [], "runtimeStatus": "unknown"})
        namespace["methodIds"].append(method["id"])
    for test in tests:
        namespace = namespaces_by_name.setdefault(test["namespace"], {"id": stable_id("coverage-namespace", test["namespace"]), "name": test["namespace"], "testIds": [], "methodIds": [], "runtimeStatus": "unknown"})
        namespace["testIds"].append(test["id"])
    namespaces = sorted(namespaces_by_name.values(), key=lambda value: value["name"])
    truncation = dict(budget.truncated, **source["truncation"], graph=index["truncated"], catalogue=catalogue["truncated"],
        scope=scope_truncated, gherkin=gherkin["truncated"], reports=report_data["truncated"], testLinks=links_truncated, response=False)
    warnings = ["Статическая связь не доказывает запуск теста. Число переходов отчёта может отличаться от числа ветвей исходника.",
        "Время отчёта не доказывает совпадение сборки с текущим исходником; отчёты не содержат результатов отдельных тестов.",
        "Только буквальное сопоставление шагов; регулярные выражения и подстановки сценариев не вычисляются."] + budget.warnings[:100]
    if any(truncation.values()):
        warnings.append("Inventory ограничен бюджетом; отсутствующие элементы не означают отсутствие тестов или ветвей.")
    result = {"schemaVersion": 1, "analyzerVersion": "test-coverage-v1", "snapshotId": snapshot_id,
        "sourceKind": "working-tree-and-snapshot", "scope": {"nodeId": node_id, "mode": "node-chain" if node_id else "repository"},
        "status": "partial" if any(truncation.values()) or budget.warnings or any(method["sourceStatus"] != "parsed" for method in methods) else "ready",
        "namespaces": namespaces, "tests": tests, "methods": methods, "conditions": conditions, "reports": report_data["reports"],
        "summary": inventory_summary(tests, methods, conditions, namespaces, report_data["reports"]), "limits": limits,
        "truncation": truncation, "warnings": warnings,
        "sourceFacts": [{"file": file, **fact} for file, fact in sorted(source["fileFacts"].items())],
        "stats": {"sourceFiles": len(budget.files), "sourceBytes": budget.bytes,
            "reportBytes": report_data["bytes"], "graphWalkSteps": work["steps"], "scopeNodes": len(scope_paths)}}
    bound_response(result, limits["maxResponseBytes"])
    return result
