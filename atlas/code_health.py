"""Объяснимое здоровье локальных объявлений, независимо от геометрии карты."""

from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

import networkx as nx

from .code_health_policy import finding, messages, policy, structural_components, weighted_score
from .documentation_health import capture_documentation_health, score_documentation, text, VERSION as DOCUMENTATION_VERSION
from . import rust_support
from .code_readability import readability_findings
from .change_review_declaration import capture_review_declaration

ANALYZER_VERSION = "code-health-v5"


def declaration_identity(declaration, source: bytes, language: str, filename: str) -> str:
    """Файл, lexical владелец и полная сигнатура исключают угадывание перегрузок."""
    owners = []
    parent = declaration.parent
    while parent:
        if language == 'rust' and parent.type in rust_support.SCOPES:
            owners.append(rust_support.scope_name(parent, source))
        if parent.type in {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration",
                           "class_definition", "type_spec", "namespace_declaration", "function_definition",
                           "function_declaration", "method_declaration", "method_definition"}:
            value = text(parent.child_by_field_name("name"), source)
            if value:
                type_parameters = parent.child_by_field_name("type_parameters")
                owners.append(value + re.sub(r"\s+", "", text(type_parameters, source)))
        parent = parent.parent
    name = text(declaration.child_by_field_name("name"), source)
    if not name and declaration.type in {"arrow_function", "function_expression"} and declaration.parent and declaration.parent.type == "variable_declarator":
        name = text(declaration.parent.child_by_field_name("name"), source)
    if not name:
        name = "<" + declaration.type + "@" + str(declaration.start_point.row + 1) + ":" + str(declaration.start_point.column) + ">"
    parameters = re.sub(r"\s+", "", text(declaration.child_by_field_name("parameters"), source))
    receiver = re.sub(r"\s+", "", text(declaration.child_by_field_name("receiver"), source))
    value = [language, filename, ".".join(reversed(owners)), name, parameters, receiver]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()[:32]


def capture_health_facts(node: dict, declaration, source: bytes, language: str,
                         *, filename: str, source_hash: str, source_kind: str = "snapshot-captured-source") -> None:
    """Обогащает уже сопоставленное объявление в существующем AST проходе."""
    from .code_health_calls import capture_call_facts
    node["healthCallFacts"] = capture_call_facts(declaration, source, language)
    node["healthParameterList"] = re.sub(r"\s+", "", text(declaration.child_by_field_name("parameters"), source))
    owners, functions = [], []
    parent = declaration.parent
    while parent:
        if language == 'rust' and parent.type in rust_support.SCOPES:
            scope = rust_support.scope_name(parent, source)
            if parent.type == 'function_item':
                functions.append(scope)
            else:
                owners.append(scope)
        if parent.type in {"class_declaration", "class_definition", "interface_declaration", "struct_declaration", "namespace_declaration"}:
            owners.append(text(parent.child_by_field_name('name'), source))
        if parent.type in {"function_definition", "function_declaration", "method_declaration", "method_definition", "local_function_statement"}:
            functions.append(text(parent.child_by_field_name('name'), source))
        parent = parent.parent
    node['healthBindingScope'] = {'owners': list(reversed(owners)), 'functions': list(reversed(functions)), 'language': language, 'file': filename}
    node["healthIdentity"] = declaration_identity(declaration, source, language, filename)
    if source_kind == "snapshot-captured-source":
        capture_review_declaration(node, declaration, source, filename, source_hash, node["healthIdentity"])
    from .code_health_source import source_stamp
    node["healthSourceStamp"] = source_stamp(declaration, source, language, source_hash)
    node["documentationHealth"] = capture_documentation_health(declaration, source, language, node.get("complexity", {}),
        filename=filename, source_hash=source_hash, source_kind=source_kind)


def dependency_profiles(graph: dict) -> dict[str, dict]:
    """Индекс и SCC описывают static calls; dispatch не суммируется.

    Позиции retained call sites привязаны к исходному хешу, даже если binding
    повторно используется после правки комментариев в рабочем тексте.
    """
    from .code_health_calls import confirmed_call_edge, resolved_call_facts

    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    targets, writes, candidates = defaultdict(set), defaultdict(set), defaultdict(set)
    outgoing = defaultdict(list)
    call_graph = nx.DiGraph()
    call_graph.add_nodes_from(nodes)
    for edge in graph.get("edges", []):
        source, target = edge.get("source"), edge.get("target")
        if source not in nodes:
            continue
        relation = edge.get("relation") or edge.get("type")
        if relation == "calls":
            outgoing[source].append(edge)
            if not confirmed_call_edge(edge, nodes[source]):
                continue
            targets[source].add(target)
            if target in nodes and nodes[target].get("kind") != "external":
                call_graph.add_edge(source, target)
        elif relation == "writes":
            writes[source].add(target)
        elif relation == "dispatches_to":
            candidates[source].add(target)
    recursive = set()
    components = list(nx.strongly_connected_components(call_graph))
    for component in components:
        if len(component) > 1 or any(call_graph.has_edge(node, node) for node in component):
            recursive.update(component)
    limited = any(bool((graph.get("metadata", {}).get(key) or {}).get("truncated"))
                  for key in ("memberGraph", "sourceStructure", "interfaceDispatch"))
    result = {}
    for identifier in nodes:
        resolved = resolved_call_facts(nodes[identifier], outgoing[identifier], nodes)
        unmatched = [call for call in resolved if call.get('resolution') != 'confirmed'] if resolved is not None else None
        absent = sum(target not in nodes for target in targets[identifier])
        unknown = absent + len(unmatched or [])
        coverage_unknown = unmatched is None
        result[identifier] = {"status": "partial" if limited or unknown or coverage_unknown else "complete", "callTargets": len(targets[identifier]),
                              "writes": len(writes[identifier]), "implementationCandidates": len(candidates[identifier]),
                              "unknownTargets": unknown if not coverage_unknown else None, "callCoverage": "unavailable" if coverage_unknown else "captured",
                              "unresolvedCalls": (unmatched or [])[:20], "recursive": identifier in recursive, "basis": "static-graph",
                              "runtimeSelectionKnown": False,
                              "resolvedCalls": [call for call in (resolved or []) if call.get('resolution') == 'confirmed'][:20],
                              "externalTargets": len({target for target in targets[identifier] if nodes.get(target, {}).get('kind') == 'external'})}
        if resolved:
            # Reusing a binding after comment edits does not relocate the stored
            # call-site positions. Both retained lists belong to these bytes.
            source = nodes[identifier].get('complexity') or {}
            result[identifier]['callSiteProvenance'] = {
                'sourceHash': source.get('sourceHash'),
                'sourceKind': source.get('sourceKind', 'unavailable'),
                'file': nodes[identifier].get('file'),
                'scope': 'resolvedCalls and unresolvedCalls use captured file coordinates',
            }
    _apply_call_paths(call_graph, components, recursive, result)
    return result


def _apply_call_paths(call_graph, components: list[set], recursive: set, profiles: dict) -> None:
    """Propagate existing SCC path facts without allocating a second NetworkX graph.

    Component adjacency and Kahn ordering stay request-local and bounded by the
    captured graph. Depth counts components, retaining the previous interpretation
    for cycles; it does not invent a finite runtime depth for recursion.
    """
    group_of = {member: group for group, members in enumerate(components) for member in members}
    successors = defaultdict(set)
    indegrees = [0] * len(components)
    for source, target in call_graph.edges:
        origin, destination = group_of[source], group_of[target]
        if origin == destination or destination in successors[origin]:
            continue
        successors[origin].add(destination)
        indegrees[destination] += 1
    pending = deque(group for group, degree in enumerate(indegrees) if degree == 0)
    order = []
    while pending:
        group = pending.popleft()
        order.append(group)
        for child in successors.get(group, ()):
            indegrees[child] -= 1
            if indegrees[child] == 0:
                pending.append(child)
    depths = [0] * len(components)
    reaches_cycle, incomplete = [False] * len(components), [False] * len(components)
    for group in reversed(order):
        members = components[group]
        children = successors.get(group, ())
        reaches_cycle[group] = not members.isdisjoint(recursive) or any(reaches_cycle[child] for child in children)
        incomplete[group] = any(profiles[member]['status'] != 'complete' for member in members) or any(incomplete[child] for child in children)
        depths[group] = 1 + max((depths[child] for child in children), default=0)
        for member in members:
            profiles[member].update(maxKnownAcyclicCallDepth=depths[group], recursiveReachable=reaches_cycle[group],
                                   callPathStatus='partial' if incomplete[group] or reaches_cycle[group] else 'complete')


def resolve_inherited_documentation(nodes: list[dict], edges: list[dict]) -> None:
    """Засчитывает inheritdoc только при единственном подтверждённом родительском контракте."""
    by_id = {n["id"]: n for n in nodes}
    bases = defaultdict(set)
    members = defaultdict(list)
    for edge in edges:
        if edge.get("relation") in {"inherits", "implements", "extends", "inherits_from"}:
            bases[edge["source"]].add(edge["target"])
    for node in nodes:
        owner = (node.get("memberFacts") or {}).get("ownerNodeId")
        if owner:
            members[owner].append(node)
    for node in nodes:
        doc = node.get("documentationHealth")
        if not doc or doc.get("version") != DOCUMENTATION_VERSION or doc.get("inheritdoc") is None or doc.get("truncated"):
            continue
        owner = (node.get("memberFacts") or {}).get("ownerNodeId")
        name = (node.get("memberFacts") or {}).get("name")
        matches = []
        for base in bases[owner]:
            for candidate in members[base]:
                inherited = candidate.get("documentationHealth") or {}
                same_name = (candidate.get("memberFacts") or {}).get("name") == name
                same_parameters = bool(node.get("healthParameterList") is not None and candidate.get("healthParameterList") == node["healthParameterList"])
                if same_name and same_parameters and inherited.get("status") == "complete" and inherited.get("ownDescription") and inherited.get("version") == DOCUMENTATION_VERSION:
                    matches.append(candidate)
        cref = doc["inheritdoc"]
        if cref:
            qualified = cref.removeprefix("M:").split("(")[0]
            matches = [n for n in matches if qualified == str(by_id.get((n.get("memberFacts") or {}).get("ownerNodeId"), {}).get("label", "")) + "." + name
                       and "(" not in cref]  # Typed cref signatures require a resolver, never guess by a substring.
        if len(matches) != 1:
            continue
        inherited = matches[0]["documentationHealth"]
        previous = {r["name"]: r for r in inherited["requirements"] if r["group"] != "explanations"}
        for requirement in doc["requirements"]:
            if requirement["group"] != "explanations" and requirement["name"] in previous:
                requirement["covered"] |= previous[requirement["name"]]["covered"]
        doc["status"] = "complete"
        doc["documented"] = bool(next((r["covered"] for r in doc["requirements"] if r["group"] == "purpose"), False))
        doc["origin"] = "resolved-inherited-contract"
        doc["inheritedFrom"] = {"nodeId": matches[0]["id"], "sourceHash": inherited["sourceHash"], "file": inherited["file"], "line": inherited["line"]}
        doc["findings"] = [f for f in doc["findings"] if f["rule"] != "unresolved-inheritdoc"]
        score_documentation(doc)
        if any(not r["covered"] for r in doc["requirements"] if r["group"] == "contract"):
            doc["findings"].append(finding("missing-contract", file=doc["file"], line=doc["line"]))


def _component_coverage(components: dict, metrics: dict, doc: dict, dependencies: dict | None,
                        organization: dict | None) -> dict:
    """Explain each component independently without filling missing scores."""
    contexts = {
        'flow': (metrics, 'local-method-syntax'),
        'size': (metrics, 'local-method-syntax'),
        'documentation': (doc, 'local-declaration-documentation'),
        'dependencies': (dependencies or {}, 'static-call-dependencies'),
        'organization': (organization or {}, 'selected-scope-file-folder'),
    }
    coverage = {}
    for name, value in components.items():
        facts, scope = contexts.get(name, ({}, 'unknown'))
        complete = isinstance(value, (int, float))
        reason = 'required-facts-complete' if complete else facts.get('reason')
        if not reason:
            reason = 'missing-required-organization-facts' if name == 'organization' else 'missing-required-' + name + '-facts'
        coverage[name] = {'status': 'complete' if complete else 'unknown', 'reason': reason,
                          'scope': facts.get('scope') or scope}
        if facts.get('sourceHash'):
            coverage[name]['sourceHash'] = facts['sourceHash']
    return coverage


def calculate_health(node: dict, dependencies: dict | None, *, organization: dict | None = None, locale: str = "en") -> dict:
    """Итоговая оценка существует лишь при полных обязательных данных; gate остаётся advisory."""
    metrics = node.get("complexity") or {}
    doc = node.get("documentationHealth") or {"status": "unavailable", "score": None, "findings": []}
    if doc.get('version') != DOCUMENTATION_VERSION:
        doc = dict(doc, score=None, status='unavailable', reason='documentation-policy-version-mismatch')
    components = structural_components(metrics, dependencies)
    components["documentation"] = doc.get("score")
    organization = organization or (node.get('health') or {}).get('organization')
    components['organization'] = (organization or {}).get('score')
    findings = deepcopy(doc.get("findings", []))
    findings.extend(deepcopy((organization or {}).get('findings', [])))
    findings.extend(readability_findings(metrics, filename=node.get("file", ""), line=node.get("line")))
    if metrics.get("status") == "complete":
        evidence = {key: metrics.get(key) for key in ("maxNestingDepth", "averageNestingDepth", "branchCount", "statementCount")}
        if metrics.get("maxNestingDepth", 0) > policy()["review"]["maxNesting"]:
            findings.append(finding("deep-nesting", file=node.get("file", ""), line=node.get("line"),
                                    severity="critical" if metrics["maxNestingDepth"] >= 6 else "warning", evidence=evidence))
        if metrics.get("statementCount", 0) > 60:
            findings.append(finding("large-method", file=node.get("file", ""), line=node.get("line"), evidence=evidence))
    if dependencies and dependencies.get("callTargets", 0) > 10:
        findings.append(finding("many-dependencies", file=node.get("file", ""), line=node.get("line"), evidence=dependencies))
    if dependencies and dependencies.get("recursive"):
        findings.append(finding("recursive-calls", file=node.get("file", ""), line=node.get("line"), severity="info"))
    active = {key: weight for key, weight in policy()["weights"].items()
              if not (key == "documentation" and doc.get("status") == "not-applicable")}
    complete = all(components.get(key) is not None for key in active)
    score = weighted_score(components, active) if complete else None
    no_body = metrics.get("reason") == "declaration has no body"
    status = "not-applicable" if no_body else "complete" if complete else "partial" if any(v is not None for v in components.values()) else "unavailable"
    if not complete and not no_body:
        findings.append(finding("unknown-analysis", file=node.get("file", ""), line=node.get("line"), severity="info"))
    for item in findings:
        item["message"] = messages(locale).get(item["rule"], item["rule"])
    warnings = any(f["severity"] in {"warning", "critical"} for f in findings)
    verdict = "REVIEW" if warnings else "UNKNOWN" if not complete else "PASS"
    from .organization_policy import transparency_score
    from .readability_assessment import assess_readability
    readability = assess_readability(metrics, findings)
    return {"version": policy()["version"], "analyzerVersion": ANALYZER_VERSION, "status": status,
            "score": None if no_body else score, "structuralScore": round(0.5 * components["flow"] + 0.25 * components["size"] + 0.25 * components["dependencies"], 1)
                if all(components[k] is not None for k in ("flow", "size", "dependencies")) else None,
            "readabilityScore": readability["score"], "readability": readability,
            "documentationScore": doc.get("score"), "organizationScore": components['organization'],
            "transparencyScore": transparency_score(components), "organization": organization,
            "components": components, "componentCoverage": _component_coverage(components, metrics, doc, dependencies, organization),
            "dependencies": dependencies,
            "findings": findings, "verdict": verdict, "mode": "advisory", "assessmentKind": "measured",
            "sourceKind": metrics.get("sourceKind", "unavailable"), "sourceHash": metrics.get("sourceHash"),
            "scope": "local-declaration plus selected-scope file/folder organization; behavioural verification separate",
            "level": "unknown" if score is None else "low" if score < 60 else "warning" if score < 80 or warnings else "good"}


def enrich_code_health(graph: dict) -> dict:
    """Дополняет новые снимки из уже захваченных AST фактов, не читая файлы второй раз."""
    profiles = dependency_profiles(graph)
    from .code_organization import organization_profiles, organization_summary
    limited = any(bool((graph.get('metadata', {}).get(key) or {}).get('truncated'))
                  for key in ('memberGraph', 'sourceStructure', 'interfaceDispatch'))
    organization = organization_profiles(graph.get('nodes', []), graph.get('edges', []), profiles, complete=not limited)
    resolve_inherited_documentation(graph.get("nodes", []), graph.get("edges", []))
    measured_at = datetime.now(timezone.utc).isoformat()
    for node in graph.get("nodes", []):
        if node.get("complexity"):
            node["health"] = calculate_health(node, profiles.get(node["id"]), organization=organization.get(node.get('file')))
            node["health"]["measuredAt"] = measured_at
    from .code_health_comparison import summarize
    summary = summarize([n for n in graph.get("nodes", []) if n.get("health")])
    summary['organization'] = organization_summary(organization)
    method_profiles = [profiles[n['id']] for n in graph.get('nodes', []) if n.get('health')]
    summary['callPath'] = {'maxKnownAcyclicDepth': max((p['maxKnownAcyclicCallDepth'] for p in method_profiles), default=None),
        'recursiveMethods': sum(p['recursive'] for p in method_profiles),
        'status': 'partial' if any(p['callPathStatus'] != 'complete' for p in method_profiles) else 'complete',
        'basis': 'longest known static chain; dispatch alternatives not added; recursion has no finite maximum'}
    graph.setdefault("metadata", {})["codeHealth"] = {"policyVersion": policy()["version"], "analyzerVersion": ANALYZER_VERSION,
                                                      "measuredAt": measured_at, "mode": "advisory", "summary": summary}
    return graph


def compact_health(node: dict) -> dict:
    """Компактный контракт не переносит документацию и сигнатуры в каждый search ответ."""
    result = {}
    if node.get("healthIdentity"):
        result["healthIdentity"] = node["healthIdentity"]
    if node.get("health"):
        result["health"] = {key: node["health"].get(key) for key in ("version", "status", "score", "readabilityScore", "documentationScore", "organizationScore", "transparencyScore", "verdict", "level", "measuredAt", "sourceKind", "sourceHash")}
        unknown = []
        for component, coverage in node['health'].get('componentCoverage', {}).items():
            if coverage.get('status') != 'complete':
                unknown.append(component + ':' + coverage.get('reason', 'required-facts-unavailable'))
        if unknown:
            result['health']['unknownReasons'] = unknown

    if (node.get("health") or {}).get("readability"):
        profile = node["health"]["readability"]
        result["health"]["readability"] = {key: profile.get(key) for key in ("version", "status", "score", "verdict", "calibration", "humanReview")}
    if node.get("documentationHealth"):
        result["documentationHealth"] = {key: node["documentationHealth"].get(key) for key in ("version", "status", "score", "eligible", "documented", "ownDescription", "origin", "semanticReview", "parameterCoverage")}
    return result
