"""Mod-driven, bounded static test entries and their declared Java inputs."""
from collections import defaultdict
from copy import deepcopy
import hashlib
from pathlib import Path

from .java_annotations import (METHODS, TYPES, attribute_facts, bind_raw_declarations,
                               MAX_AST_NODES, parse_java, public_annotation, text)
from .source_viewer import SourceError, _read_regular_file
from .test_discovery_data import argument_nodes, annotation_strings, data_sources, resource_references
from .test_dataset_contract import parameters_and_bindings

VERSION = "static-test-discovery-v2"
ORIGIN = "java-annotations"
MAX_FILES = 800
MAX_BYTES = 32 * 1024 * 1024
MAX_DECLARATIONS = 30_000
MAX_DERIVED = 15_000
MAX_ENTRIES = 5_000
MAX_TOTAL_AST_NODES = 1_500_000
MAX_COMPOSED_DEPTH = 8


def _warn(metadata: dict, message: str) -> None:
    """Retain a bounded warning sample while every failure keeps coverage partial."""
    metadata["status"] = "partial"
    if len(metadata["warnings"]) < 20:
        metadata["warnings"].append(message[:350])


def _relative(root: Path, filename: str) -> str:
    """Accept a graph source inside its project, without resolving source symlinks."""
    path = Path(filename)
    if path.is_absolute():
        path = path.relative_to(root)
    if not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError("Invalid project-relative source path")
    return path.as_posix()


def _annotation_matches(annotation: dict, frameworks: list[dict], local: dict,
                        seen: frozenset = frozenset(), depth: int = 0) -> list[tuple]:
    """Match exact FQNs and bounded local meta-annotations; cycles never invent a role."""
    qualified = annotation.get("qualifiedName")
    if not qualified or qualified in seen or depth >= MAX_COMPOSED_DEPTH:
        return []
    results = []
    for framework in frameworks:
        for rule in framework.get("annotationRules", []):
            if rule.get("name") == qualified:
                results.append((annotation, rule, framework, depth > 0))
    declaration = local.get(qualified)
    if declaration:
        composed_frameworks = [item for item in frameworks if item.get("supportsComposed")]
        for meta in declaration["annotations"]:
            results.extend(_annotation_matches(meta, composed_frameworks, local,
                                               seen | {qualified}, depth + 1))
    return results


def _type_context(record: dict, declarations: list[dict]) -> list[dict]:
    """Return lexical containing types nearest first; scope does not imply inheritance."""
    found = []
    for item in declarations:
        if item["file"] != record["file"] or item["kind"] not in TYPES or item is record:
            continue
        if item["_ast"].start_byte <= record["_ast"].start_byte and item["_ast"].end_byte >= record["_ast"].end_byte:
            found.append(item)
    return sorted(found, key=lambda item: (item["_ast"].end_byte - item["_ast"].start_byte,
                                            -item["_ast"].start_byte))


def _base_types(types: list[dict], index: dict) -> list[dict]:
    """Follow only a unique source-declared superclass, retaining cycles as a boundary."""
    bases, seen = [], {item["qualifiedName"] for item in types}
    current = types[0] if types else None
    while current and len(bases) < MAX_COMPOSED_DEPTH:
        name = current.get("base", "")
        if not name:
            break
        qualified, _ = current["unit"].resolve(name, set(index))
        candidate = index.get(qualified)
        if candidate is None or qualified in seen:
            break
        seen.add(qualified)
        bases.append(candidate)
        current = candidate
    return bases


def _unique_types(declarations: list[dict], kinds: set[str]) -> dict:
    """Ambiguous qualified declarations do not pick a last-file winner."""
    candidates = defaultdict(list)
    for declaration in declarations:
        if declaration["kind"] in kinds:
            candidates[declaration["qualifiedName"]].append(declaration)
    return {name: values[0] for name, values in candidates.items() if len(values) == 1}


def _scoped_annotations(record: dict, types: list[dict], bases: list[dict]) -> list[dict]:
    """Keep declaration, containing type, inherited type and parameter ownership explicit."""
    annotations = []
    for scope, owner in [("declaration", record), *[("type", item) for item in types],
                         *[("inherited-type", item) for item in bases]]:
        for annotation in owner["annotations"]:
            annotations.append({**public_annotation(annotation), "scope": scope,
                                "ownerNodeId": owner.get("nodeId"), "ownerName": owner["name"]})
    for parameter in record["parameters"]:
        for annotation in parameter["annotations"]:
            annotations.append({**public_annotation(annotation), "scope": "parameter",
                                "ownerNodeId": record["nodeId"], "parameterName": parameter["name"],
                                "position": parameter["position"]})
    return annotations


def _fixtures(record: dict, types: list[dict], bases: list[dict], declarations: list[dict],
              frameworks: list[dict], local: dict) -> list[dict]:
    """Expose declared lifecycle sources separately, without promising callback execution/order."""
    owners = {item["qualifiedName"]: item not in types for item in types + bases}
    results = []
    for method in declarations:
        if method["kind"] not in METHODS:
            continue
        owner = method["qualifiedName"].rsplit(".", 1)[0]
        if owner not in owners:
            continue
        for annotation in method["annotations"]:
            matches = _annotation_matches(annotation, frameworks, local)
            for matched, rule, framework, composed in matches:
                if rule["role"] != "lifecycle":
                    continue
                results.append({"kind": "lifecycle", "label": method["name"],
                                "framework": framework["id"], "annotation": public_annotation(matched),
                                "nodeIds": [method["nodeId"]] if method.get("nodeId") else [],
                                "file": method["file"], "line": method["line"],
                                "inherited": owners[owner], "composed": composed,
                                "phase": {"BeforeAll": "before-all", "BeforeEach": "before-each",
                                          "AfterAll": "after-all", "AfterEach": "after-each"}.get(
                                              matched["qualifiedName"].rsplit(".", 1)[-1], "extension"),
                                "scope": "inherited-type" if owners[owner] else "type",
                                "sourceHash": method["unit"].source_hash,
                                "status": "declared", "effectiveOrder": "unknown",
                                "resources": resource_references(method)})
    return results


def _entry(record: dict, types: list[dict], bases: list[dict], declarations: list[dict],
           frameworks: list[dict], local: dict, raw: dict) -> dict | None:
    """Create an ingress only from a matched test declaration; labels remain metadata."""
    own = []
    for annotation in record["annotations"]:
        own.extend(_annotation_matches(annotation, frameworks, local))
    tests = [item for item in own if item[1]["role"] == "test"]
    if not tests:
        return None
    framework = tests[0][2]
    label, tags, sources, disabled = record["name"], [], [], []
    display_template = None
    status_unknown = False
    metadata = []
    for owner in [record, *types, *bases]:
        for annotation in owner["annotations"]:
            for matched, rule, matched_framework, composed in _annotation_matches(annotation, frameworks, local):
                argument = rule.get("argument") or "value"
                strings = annotation_strings(matched, matched["_unit"].content, argument)
                role = rule["role"]
                if role == "test" and owner is record:
                    display_argument = rule.get("displayArgument") or "description"
                    display = annotation_strings(matched, matched["_unit"].content, display_argument)
                    if display:
                        if display_argument == "name":
                            display_template = display[0]
                        elif label == record["name"]:
                            label = display[0]
                    enabled_argument = rule.get("disabledArgument")
                    if enabled_argument:
                        enabled_values = argument_nodes(matched, enabled_argument)
                        if enabled_values:
                            expression = text(enabled_values[0], matched["_unit"].content)
                            if expression == "false":
                                disabled.append({"reason": matched["text"], "file": matched["file"],
                                                 "line": matched["line"], "scope": "declaration"})
                            elif expression != "true":
                                status_unknown = True
                    if rule.get("sourceArgument"):
                        source_values = annotation_strings(matched, matched["_unit"].content, rule["sourceArgument"])
                        for value in source_values:
                            sources.append({"kind": "provider-reference", "label": value,
                                            "expression": matched["text"], "file": matched["file"],
                                            "line": matched["line"], "nodeIds": [], "status": "unresolved",
                                            "reason": "Library provider declared; execution/binding is not implemented."})
                if role == "disabled" and owner not in bases:
                    disabled.append({"reason": strings[0] if strings else matched["text"],
                                     "file": matched["file"], "line": matched["line"],
                                     "scope": "declaration" if owner is record else "type"})
                if role == "metadata":
                    metadata.append({"name": matched["qualifiedName"], "values": strings,
                                     "composed": composed, "file": matched["file"], "line": matched["line"]})
                    if owner is record and matched["qualifiedName"].rsplit(".", 1)[-1] == "DisplayName" and strings:
                        label = strings[0]
                    if matched["qualifiedName"].rsplit(".", 1)[-1] in {"Tag", "Category"}:
                        tags.extend(strings)
                if owner is record and role == "parameter-source":
                    sources.extend(data_sources(matched, rule, record, declarations))
    parameters, contexts = parameters_and_bindings(record, sources, local)
    annotations = _scoped_annotations(record, types, bases)
    digest = hashlib.sha256(f"{record['nodeId']}:{framework['id']}".encode()).hexdigest()[:20]
    return {"version": VERSION, "id": "test-entry-" + digest, "kind": "test", "label": label,
            "containerNodeId": types[0].get("nodeId") if types else None,
            "parameters": parameters, "context": contexts,
            "framework": framework["id"], "language": "java", "modId": framework.get("modId"),
            "handlerNodeIds": [record["nodeId"]], "file": record["file"], "line": record["line"],
            "codeProjectId": raw.get("codeProjectId"), "codeProjectFacts": deepcopy(raw.get("codeProjectFacts")),
            "evidence": [{"kind": "annotation", "name": item[0].get("qualifiedName"),
                          "file": item[0]["file"], "line": item[0]["line"], "composed": item[3]}
                         for item in tests],
            "confidence": "source-declaration", "unresolved": [],
            "annotations": annotations, "dataSources": sources,
            "fixtures": _fixtures(record, types, bases, declarations, frameworks, local),
            "tags": list(dict.fromkeys(tags)), "metadata": metadata,
            "declaredStatus": "disabled" if disabled else "unknown" if status_unknown else "enabled",
            "disabledReason": disabled[0]["reason"] if disabled else None,
            "displayTemplate": display_template,
            "disabledEvidence": disabled,
            "runtimeStatus": "unknown",
            "sourceKind": "snapshot-captured-source"}


def _capture_annotations(record: dict, raw: dict, nodes: list, edges: list) -> None:
    """Add independent annotation occurrence cards owned by their exact raw declaration."""
    occurrences = [("declaration", item, None) for item in record["annotations"]]
    for parameter in record["parameters"]:
        occurrences.extend(("parameter", item, parameter) for item in parameter["annotations"])
    for scope, annotation, parameter in occurrences:
        if len(nodes) >= MAX_DERIVED:
            return
        key = f"{record['nodeId']}:{scope}:{annotation['line']}:{annotation['column']}"
        identity = "atlas-attribute:java:" + hashlib.sha256(key.encode()).hexdigest()[:24]
        owner_id = None if parameter else record["nodeId"]
        annotation["nodeId"] = identity
        fact = {**public_annotation(annotation), "scope": scope, "ownerNodeId": owner_id,
                "declarationNodeId": record["nodeId"]}
        if parameter:
            fact.update(parameterName=parameter["name"], position=parameter["position"])
        owner = {"scope": scope, "name": parameter["name"] if parameter else record["name"],
                 "file": record["file"], "line": annotation["line"]}
        if parameter:
            owner["position"] = parameter["position"]
        nodes.append({**public_annotation(annotation), "id": identity, "label": annotation["text"], "kind": "attribute",
                      "file": annotation["file"], "line": annotation["line"],
                      "_origin": ORIGIN, "ownerNodeId": owner_id,
                      "declarationNodeId": record["nodeId"], "owner": owner, "scope": scope,
                      "sourceHash": record["unit"].source_hash,
                      "annotationFacts": fact, "attributeFacts": {"kind": "occurrence", **fact},
                      "codeProjectId": raw.get("codeProjectId"),
                      "codeProjectFacts": deepcopy(raw.get("codeProjectFacts"))})
        for group in raw["attributeFacts"]["groups"]:
            if group["scope"] == scope and (not parameter or group["name"] == parameter["name"]):
                group.setdefault("attrNodeIds", []).append(identity)
        edges.append({"source": record["nodeId"], "target": identity,
                      "relation": "has_parameter_attribute" if parameter else "has_attribute",
                      "scope": scope, "ownerNodeId": record["nodeId"],
                      "confidence": "SOURCE", "_origin": ORIGIN})


def enrich_test_discovery(graph: dict, root: Path, rules: dict) -> dict:
    """Capture mod-enabled Java test declarations without executing analyzed code.

    Only source paths already present in the captured graph are read. Existing
    raw IDs/edges are retained. Skipped, ambiguous and over-budget facts remain
    explicitly incomplete; this inventory is never a test run or coverage report.
    """
    root = Path(root).resolve()
    nodes = [deepcopy(item) for item in graph.get("nodes", []) if item.get("_origin") != ORIGIN]
    edges = [deepcopy(item) for item in graph.get("edges", []) if item.get("_origin") != ORIGIN]
    result = {**graph, "nodes": nodes, "edges": edges, "metadata": deepcopy(graph.get("metadata") or {})}
    frameworks = [item for item in rules.get("testFrameworks", []) if item.get("language") == "java"]
    metadata = {"version": VERSION, "status": "complete" if frameworks else "disabled",
                "analysisFingerprint": rules.get("fingerprint"), "entries": [], "warnings": [],
                "coverage": {"files": 0, "fileAttempts": 0, "bytes": 0, "astNodes": 0, "declarations": 0, "boundDeclarations": 0,
                             "unresolvedDeclarations": 0, "testEntries": 0},
                "sourceKind": "snapshot-captured-source",
                "scope": "Static Java declaration inventory; providers, fixtures and tests are not executed."}
    result["metadata"]["testDiscovery"] = metadata
    by_file = defaultdict(list)
    for node in nodes:
        filename = str(node.get("file") or node.get("source_file") or "")
        if filename.lower().endswith(".java"):
            node.pop("testFacts", None)
            node.pop("testContainerFacts", None)
            try:
                by_file[_relative(root, filename)].append(node)
            except ValueError:
                _warn(metadata, "A Java source path is outside the project and was skipped.")
    if not frameworks:
        return result
    known = {rule["name"] for item in frameworks for rule in item.get("annotationRules", [])}
    units, declarations = [], []
    for filename, file_nodes in sorted(by_file.items()):
        if (metadata["coverage"]["fileAttempts"] >= MAX_FILES or metadata["coverage"]["bytes"] >= MAX_BYTES
                or len(declarations) >= MAX_DECLARATIONS or metadata["coverage"]["astNodes"] >= MAX_TOTAL_AST_NODES):
            _warn(metadata, "Java test-discovery source budget exhausted.")
            break
        metadata["coverage"]["fileAttempts"] += 1
        allowance = min(1024 * 1024, MAX_BYTES - metadata["coverage"]["bytes"])
        # A failed reader may have consumed its full allowance before rejecting source.
        # Reserving first bounds repeated invalid encodings and unavailable-file attempts.
        metadata["coverage"]["bytes"] += allowance
        try:
            content = _read_regular_file(root, Path(filename), allowance)
            metadata["coverage"]["bytes"] -= allowance - len(content)
            unit = parse_java(content, filename, known, min(MAX_AST_NODES, MAX_TOTAL_AST_NODES - metadata["coverage"]["astNodes"]))
        except (SourceError, OSError, UnicodeError, ImportError, ValueError, TypeError) as error:
            _warn(metadata, f"Java source unavailable: {filename}: {type(error).__name__}")
            continue
        remaining = MAX_DECLARATIONS - len(declarations)
        if len(unit.declarations) > remaining:
            unit.declarations = unit.declarations[:remaining]
            unit.truncated = True
        bind_raw_declarations(unit, file_nodes)
        units.append(unit)
        declarations.extend(unit.declarations)
        metadata["coverage"]["astNodes"] += unit.inspected_nodes
        if unit.truncated or unit.parse_errors:
            _warn(metadata, f"Java source is partial: {filename}")
    local = _unique_types(declarations, {"annotation_type_declaration"})
    type_index = _unique_types(declarations, TYPES)
    known.update(type_index)
    # Resolve custom annotation names only after all bounded source declarations exist.
    for record in declarations:
        containing = _type_context(record, declarations)
        inherited = _base_types(containing, type_index)
        for annotations in [record["annotations"], *[item["annotations"] for item in record["parameters"]]]:
            for annotation in annotations:
                annotation["qualifiedName"], annotation["resolution"] = record["unit"].resolve(annotation["name"], known, record["owner"])
                if "." not in annotation["name"]:
                    for base in inherited:
                        if base["qualifiedName"] + "." + annotation["name"] in known:
                            annotation.update(qualifiedName=None, resolution="possible-inherited-type-shadow")
                            break
    raw_by_id = {node["id"]: node for node in nodes}
    derived, derived_edges = [], []
    for record in declarations:
        if not record.get("nodeId"):
            metadata["coverage"]["unresolvedDeclarations"] += 1
            continue
        raw = raw_by_id[record["nodeId"]]
        types = _type_context(record, declarations)
        raw["attributeFacts"] = attribute_facts(record, types)
        _capture_annotations(record, raw, derived, derived_edges)
        metadata["coverage"]["boundDeclarations"] += 1
        if record["kind"] not in METHODS or record["_ast"].has_error:
            continue
        entry = _entry(record, types, _base_types(types, type_index), declarations, frameworks, local, raw)
        if entry and len(metadata["entries"]) < MAX_ENTRIES:
            metadata["entries"].append(entry)
            raw["testFacts"] = deepcopy(entry)
            for source in entry["dataSources"]:
                evidence = source.get("declarationLocation") or {}
                for provider_id in source.get("nodeIds", []):
                    if provider_id not in raw_by_id:
                        continue
                    derived_edges.append({"source": record["nodeId"], "target": provider_id,
                                          "relation": "uses_test_data", "confidence": "SOURCE",
                                          "dataSourceId": source["id"], "_origin": ORIGIN,
                                          "sourceKind": "snapshot-captured-source", **evidence,
                                          "runtimeStatus": "unknown"})
    nodes.extend(derived)
    edges.extend(derived_edges)
    metadata["coverage"].update(files=len(units), declarations=len(declarations), testEntries=len(metadata["entries"]))
    source_digest = hashlib.sha256()
    for unit in units:
        source_digest.update(f"{unit.file}:{unit.source_hash}\n".encode())
    metadata["sourceFingerprint"] = source_digest.hexdigest()
    if len(derived) >= MAX_DERIVED or len(metadata["entries"]) >= MAX_ENTRIES:
        _warn(metadata, "Derived annotation/test-entry budget reached.")
    if metadata["warnings"] or metadata["coverage"]["unresolvedDeclarations"]:
        metadata["status"] = "partial"
    from .test_containers import capture_test_containers
    capture_test_containers(nodes, metadata["entries"], metadata)
    return result
