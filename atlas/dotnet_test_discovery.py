"""Mod-enabled C# test entries reuse the bounded inventory; analyzed code never runs."""
from collections import defaultdict
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from .attribute_facts import attribute_facts
from .test_coverage import LIMITS as DEFAULT_LIMITS
from .test_coverage_sources import (METHOD_TYPES, TYPE_TYPES, SourceBudget, declaration_status,
                                    index_raw_files, literal_string, namespace_of, raw_line,
                                    scan_csharp, source_text, stable_id, walk)

VERSION = "dotnet-test-discovery-v1"
FRAMEWORKS = {"xunit", "nunit", "mstest"}
MAX_FACTS_PER_TEST = 200
INLINE_ROWS = {"InlineData", "TestCase", "DataRow"}
PROVIDERS = {"MemberData", "ClassData", "TestCaseSource", "ValueSource", "DynamicData"}
WARNING = ("Static C# declarations only; providers and tests are not executed. "
           "Inheritance, other partial declarations and effective lifecycle order remain unknown.")


def _canonical(name: str) -> str:
    """Normalize only C# Attribute suffix syntax; this is not type resolution."""
    name = name.removeprefix("global::")
    return name if name.endswith("Attribute") else name + "Attribute"


def _namespace_scopes(ast) -> list:
    """Return lexical using-directive scopes; a sibling namespace cannot supply an import."""
    scopes = [None]
    parent = ast.parent
    while parent is not None:
        if parent.type == "namespace_declaration":
            scopes.append(parent.start_byte)
        parent = parent.parent
    return scopes


def _source_contexts(budget: SourceBudget, limits: dict) -> tuple[dict, set]:
    """Index already-read AST declarations/imports without a second filesystem read."""
    from tree_sitter import Language, Parser
    import tree_sitter_c_sharp
    parser = Parser(Language(tree_sitter_c_sharp.language()))
    contexts, local_types, inspected, global_usings = {}, set(), 0, []
    for filename, source in budget.files.items():
        if not source:
            continue
        content = source["content"]
        tree = parser.parse(content)
        context = {"source": source, "usings": [], "namespace": "", "methods": {}}
        types = []
        for item in walk(tree.root_node):
            inspected += 1
            if inspected > limits["maxAstNodes"]:
                budget.truncated["astNodes"] = True
                return contexts, local_types
            if item.type == "file_scoped_namespace_declaration":
                context["namespace"] = source_text(item.child_by_field_name("name"), content)
            elif item.type == "using_directive":
                value = source_text(item, content).strip()
                imported = re.fullmatch(r"(?:global\s+)?using\s+([\w.]+)\s*;", value)
                alias = re.fullmatch(r"(?:global\s+)?using\s+(\w+)\s*=\s*([\w.:]+)\s*;", value)
                using = {"scope": _namespace_scopes(item)[-1],
                         "import": imported[1] if imported else None,
                         "alias": (alias[1], alias[2]) if alias else None}
                if value.startswith("global "):
                    global_usings.append(using)
                else:
                    context["usings"].append(using)
            elif item.type in TYPE_TYPES:
                types.append(item)
            elif item.type in METHOD_TYPES:
                name = source_text(item.child_by_field_name("name"), content)
                context["methods"][(item.start_point.row + 1, item.start_point.column + 1, name)] = item
        for item in types:
            name = source_text(item.child_by_field_name("name"), content)
            namespace = namespace_of(item, content, context["namespace"])
            local_types.add(_canonical(name if namespace == "<global>" else namespace + "." + name))
        contexts[filename] = context
    for context in contexts.values():
        context["globalUsings"] = global_usings
    return contexts, local_types


def _method_context(context: dict, ast) -> dict:
    """Combine captured global usings with this declaration's lexical namespace scopes."""
    scopes = _namespace_scopes(ast)
    imports, aliases = set(), {}
    directives = list(context.get("globalUsings", []))
    for scope in [None, *reversed(scopes[1:])]:
        directives.extend(item for item in context["usings"] if item["scope"] == scope)
    for directive in directives:
        if directive["import"]:
            imports.add(directive["import"])
        if directive["alias"]:
            name, target = directive["alias"]
            aliases[name] = target
    return {**context, "imports": imports, "aliases": aliases}


def _qualified(attribute: dict, context: dict, namespace: str, known: set, local: set) -> str | None:
    """Require a known FQN/import and reject captured local attributes with the same name."""
    name = str(attribute.get("name") or "").removeprefix("global::")
    head, dot, tail = name.partition(".")
    alias = context["aliases"].get(head)
    if alias:
        name = alias + ("." + tail if dot else "")
    canonical = _canonical(name)
    if "." in name:
        return canonical if canonical in known and canonical not in local else None
    own = _canonical(name if namespace == "<global>" else namespace + "." + name)
    if own in local:
        return None
    candidates = {prefix + "." + canonical for prefix in context["imports"]}
    if candidates & local:
        return None
    matches = candidates & known
    return next(iter(matches)) if len(matches) == 1 else None


def _scoped_facts(raw: dict, method: dict, context: dict, ast, known: set, local: set) -> list[dict]:
    """Reuse matching captured attributes, or capture this exact bounded AST declaration."""
    captured = raw.get("attributeFacts") or {}
    source = context["source"]
    if captured.get("sourceHash") != source["sourceHash"] or captured.get("status") not in {"parsed", "partial"}:
        captured = attribute_facts(ast, source["content"], source["file"])
    result = []
    for group in captured.get("groups", []):
        for attribute in group.get("attributes", []):
            owner = raw["id"] if group["scope"] == "declaration" else group.get("ownerNodeId")
            result.append({**deepcopy(attribute), "scope": group["scope"],
                           "ownerName": group.get("name"), "ownerNodeId": owner,
                           "declarationNodeId": raw["id"] if group["scope"] == "parameter" else None,
                           "parameterName": group.get("name") if group["scope"] == "parameter" else None,
                           "position": group.get("position"),
                           "qualifiedName": _qualified(attribute, context, method["namespace"], known, local),
                           "sourceKind": "snapshot-captured-source"})
    return result[:MAX_FACTS_PER_TEST]


def _method_records(inventory: dict, contexts: dict, raw_nodes: dict, known: set, local: set) -> dict:
    """Keep only exact raw-owner ranges; a missing/ambiguous owner never becomes an ingress."""
    records = {}
    for method in inventory["methods"]:
        raw = raw_nodes.get(method.get("ownerNodeId"))
        context = contexts.get(method["file"])
        if not raw or not context or raw_line(raw) is None:
            continue
        key = method["line"], method["column"], method["name"]
        ast = context["methods"].get(key)
        if ast is None or ast.type != "method_declaration" or method["sourceStatus"] != "parsed":
            continue
        facts = _scoped_facts(raw, method, _method_context(context, ast), ast, known, local)
        # The nearest lexical type identifies sibling fixtures; inheritance is not inferred.
        parent = ast.parent
        while parent is not None and parent.type not in TYPE_TYPES:
            parent = parent.parent
        owner = (method["file"], parent.start_byte) if parent is not None else None
        records[method["id"]] = {"method": method, "raw": raw, "facts": facts, "owner": owner}
    return records


def _skip_config(attribute: dict) -> dict:
    """Convert one captured annotation to the existing declared-skip contract, keeping expressions raw."""
    arguments = []
    for item in attribute.get("arguments", []):
        expression = item["expression"]
        if item.get("name"):
            expression = f"{item['name']}={expression}"
        arguments.append(expression)
    short = attribute["qualifiedName"].rsplit(".", 1)[-1].removesuffix("Attribute")
    return {"shortName": short, "arguments": arguments}


def _data_sources(facts: list[dict], allowed: dict) -> list[dict]:
    """Expose literal source rows or unresolved provider references without evaluating either."""
    sources = []
    for attribute in facts:
        rule = allowed.get(attribute.get("qualifiedName"))
        if not rule or attribute["scope"] not in {"declaration", "parameter"}:
            continue
        short = attribute["qualifiedName"].rsplit(".", 1)[-1].removesuffix("Attribute")
        if rule["role"] != "parameter-source" and short not in INLINE_ROWS | PROVIDERS:
            continue
        source = {"kind": "provider-reference", "label": attribute["name"],
                  "expression": attribute.get("text") or attribute["name"],
                  "arguments": deepcopy(attribute.get("arguments", [])),
                  "file": attribute["file"], "line": attribute["line"], "nodeIds": [],
                  "parameterName": attribute.get("parameterName"), "sourceKind": "snapshot-captured-source"}
        if short in INLINE_ROWS or short == "Values":
            # A row name belongs to that row, not to the aggregate parameterized method.
            arguments = [item for item in attribute.get("arguments", []) if not item.get("name")]
            row_status, row_reasons = declaration_status([_skip_config(attribute)])
            row_label = None
            for argument in attribute.get("arguments", []):
                if argument.get("name") in {"TestName", "DisplayName"}:
                    row_label = literal_string(argument["expression"])
            source.update(kind="parameter-values" if short == "Values" else "inline-data", status="declared",
                          rows=[{"expression": source["expression"], "arguments": deepcopy(arguments),
                                 "declaredStatus": row_status, "skipReasons": row_reasons,
                                 "label": row_label, "file": attribute["file"], "line": attribute["line"]}], declaredRowCount=1)
            if short == "Values":
                source["declaredValueCount"] = len(arguments)
                source["reason"] = "Parameter values are declared; cross-parameter combinations are not expanded."
        else:
            source.update(status="unresolved", reason="Provider binding and dataset evaluation are not implemented.")
        sources.append(source)
    return sources


def _fixtures(records: dict, allowed: dict) -> dict:
    """Index lexical lifecycle declarations once; effective invocation/order remains unknown."""
    result = defaultdict(list)
    for record in records.values():
        for attribute in record["facts"]:
            rule = allowed.get(attribute.get("qualifiedName"))
            if not rule or rule["role"] != "lifecycle" or attribute["scope"] != "declaration":
                continue
            method = record["method"]
            result[record["owner"]].append({"kind": "lifecycle", "label": method["name"], "framework": rule["framework"],
                "annotation": deepcopy(attribute), "nodeIds": [record["raw"]["id"]],
                "file": method["file"], "line": method["line"], "status": "declared",
                "inherited": False, "effectiveOrder": "unknown"})
    return result


def _entry(test: dict, record: dict, framework: dict, fixtures: dict) -> dict | None:
    """Require a mod-authorized declaration and keep declared disablement separate from runtime."""
    allowed = {_canonical(rule["name"]): rule for rule in framework.get("annotationRules", [])}
    evidence, config, tags, row_skips = [], [], [], []
    label = test["name"]
    for attribute in record["facts"]:
        rule = allowed.get(attribute.get("qualifiedName"))
        if not rule or attribute["scope"] not in {"declaration", "type"}:
            continue
        short = attribute["qualifiedName"].rsplit(".", 1)[-1].removesuffix("Attribute")
        args = attribute.get("arguments", [])
        if rule["role"] == "test" and attribute["scope"] == "declaration":
            evidence.append({"kind": "annotation", "name": attribute["qualifiedName"],
                             "file": attribute["file"], "line": attribute["line"]})
            for argument in args:
                if argument.get("name") in {"DisplayName", "TestName"} and short != "TestCase":
                    label = literal_string(argument["expression"]) or label
        if rule["role"] in {"test", "disabled"}:
            skip = _skip_config(attribute)
            if short in INLINE_ROWS:
                _, reasons = declaration_status([skip])
                row_skips.extend(reasons)
            else:
                config.append(skip)
        if rule["role"] == "metadata":
            tags.extend(value for item in args if (value := literal_string(item["expression"])) is not None)
    if not evidence:
        return None
    declared, reasons = declaration_status(config)
    # A skipped parameter row does not disable every invocation of its method.
    if declared == "enabled" and row_skips:
        declared = "unknown"
        reasons = ["Skip configuration is declared for individual parameter rows, not the entire test method.", *row_skips]
    raw = record["raw"]
    return {"id": stable_id("test-entry", raw["id"], framework["id"]), "kind": "test",
            "framework": framework["id"], "language": "csharp", "modId": framework.get("modId"),
            "handlerNodeIds": [raw["id"]], "codeProjectId": raw.get("codeProjectId"),
            "codeProjectFacts": deepcopy(raw.get("codeProjectFacts")), "label": label,
            "file": test["file"], "line": test["line"], "evidence": evidence,
            "sourceKind": "snapshot-captured-source", "confidence": "source-declaration",
            "declaredStatus": declared, "disabledReason": reasons[0] if reasons else None,
            "skipReasons": reasons, "runtimeStatus": "unknown", "unresolved": [WARNING],
            "annotations": deepcopy(record["facts"]), "dataSources": _data_sources(record["facts"], allowed),
            "fixtures": [deepcopy(item) for item in fixtures.get(record["owner"], [])[:MAX_FACTS_PER_TEST]
                         if item["framework"] == framework["id"]],
            "tags": list(dict.fromkeys(tags))}


def _merge(graph: dict, rules: dict) -> tuple[dict, dict, list]:
    """Replace only this adapter's facts; Java entries and all graph relations stay intact."""
    nodes = [dict(node) for node in graph.get("nodes", [])]
    for node in nodes:
        if (node.get("testFacts") or {}).get("language") == "csharp":
            node.pop("testFacts", None)
    result = {**graph, "nodes": nodes, "metadata": deepcopy(graph.get("metadata") or {})}
    metadata = result["metadata"].setdefault("testDiscovery", {"version": "static-test-discovery-v1", "entries": []})
    metadata["entries"] = [item for item in metadata.get("entries", []) if item.get("language") != "csharp"]
    old_warnings = set((metadata.get("dotnet") or {}).get("warnings", []))
    metadata["warnings"] = [item for item in metadata.get("warnings", []) if item not in old_warnings]
    frameworks = [item for item in rules.get("testFrameworks", [])
                  if item.get("language") == "csharp" and item.get("id") in FRAMEWORKS]
    provider = {"version": VERSION, "status": "complete" if frameworks else "disabled", "warnings": [],
                "frameworks": [item["id"] for item in frameworks], "sourceKind": "snapshot-captured-source",
                "coverage": {"files": 0, "bytes": 0, "testEntries": 0}, "scope": WARNING}
    metadata["dotnet"] = provider
    metadata["analysisFingerprint"] = rules.get("fingerprint")
    return result, provider, frameworks


def _capture_sources(graph: dict, result: dict, root: Path, provider: dict) -> tuple:
    """Read graph-owned source paths once and return bounded syntax indexes and their budget."""
    raw_graph = {**graph, "nodes": [node for node in result["nodes"]
                 if node.get("kind") in {"symbol", "method", "function"} and not node.get("_origin")]}
    source_graph = {**graph, "nodes": [node for node in result["nodes"]
                    if node.get("kind") not in {"external", "attribute", "attribute_scope"}]}
    indexed = index_raw_files(root, source_graph)
    files = sorted(filename for filename in indexed if filename.lower().endswith(".cs"))
    safe_ids = {node["id"] for filename in files for node in indexed[filename]}
    if any(str(node.get("file") or node.get("source_file") or "").lower().endswith(".cs")
           and node["id"] not in safe_ids for node in source_graph["nodes"]):
        provider["warnings"].append("Some C# source paths are unavailable or outside the project; they were not read.")
    limits = dict(DEFAULT_LIMITS)
    budget = SourceBudget(root, str((graph.get("metadata") or {}).get("snapshotId") or "capture"), limits)
    try:
        inventory = scan_csharp(budget, raw_graph, files, limits)
        contexts, local = _source_contexts(budget, limits)
    except (ImportError, UnicodeError, ValueError, TypeError, OSError) as error:
        provider["warnings"].append(f"C# test inventory unavailable: {type(error).__name__}.")
        inventory, contexts, local = {"methods": [], "tests": [], "truncation": {}}, {}, set()
    return inventory, contexts, local, budget


def _publish_entries(result: dict, frameworks: list, inventory: dict, contexts: dict,
                     local: set, provider: dict) -> int:
    """Attach confirmed entries to copied raw nodes and return the unresolved declaration count."""
    metadata = result["metadata"]["testDiscovery"]
    known = {_canonical(rule["name"]) for framework in frameworks for rule in framework.get("annotationRules", [])}
    raw_nodes = {node["id"]: node for node in result["nodes"]}
    records = _method_records(inventory, contexts, raw_nodes, known, local)
    framework_index = {item["id"]: item for item in frameworks}
    allowed = {_canonical(rule["name"]): {**rule, "framework": framework["id"]}
               for framework in frameworks for rule in framework.get("annotationRules", [])}
    fixtures = _fixtures(records, allowed)
    count = 0
    for record in records.values():
        for framework in frameworks:
            entry = _entry(record["method"], record, framework, fixtures)
            if entry is None:
                continue
            if count >= DEFAULT_LIMITS["maxTests"]:
                provider["warnings"].append("C# test-entry capture budget reached.")
                return 0
            metadata["entries"].append(entry)
            raw_nodes[record["raw"]["id"]]["testFacts"] = deepcopy(entry)
            count += 1
    bound_ids = {record["raw"]["id"] for record in records.values()}
    return sum(test["framework"] in framework_index and
               (test.get("nodeId") not in bound_ids or test["frameworkResolution"] != "lexical-namespace")
               for test in inventory["tests"])


def _finish_metadata(metadata: dict, provider: dict, budget: SourceBudget,
                     inventory: dict, contexts: dict, unresolved: int) -> None:
    """Publish capture provenance and incompleteness separately from runtime results."""
    provider["warnings"].extend(budget.warnings[:20])
    if unresolved:
        provider["warnings"].append("Some candidate C# test declarations could not be bound to a confirmed raw framework method.")
    if any(budget.truncated.values()) or any(inventory["truncation"].values()):
        provider["warnings"].append("C# test-discovery source/AST inventory budget reached.")
    provider["coverage"].update(files=len(contexts), bytes=budget.bytes,
                                unresolvedTestDeclarations=unresolved,
                                testEntries=sum(item.get("language") == "csharp" for item in metadata["entries"]))
    digest = hashlib.sha256()
    for filename, source in sorted(budget.files.items()):
        if source:
            digest.update(f"{filename}:{source['sourceHash']}\n".encode())
    provider["sourceFingerprint"] = digest.hexdigest()
    provider["status"] = "partial" if provider["warnings"] else "complete"
    metadata["warnings"] = list(dict.fromkeys(metadata["warnings"] + provider["warnings"]))[:40]
    java_partial = bool(metadata.get("coverage", {}).get("unresolvedDeclarations"))
    metadata["status"] = "partial" if metadata["warnings"] or java_partial else "complete"
    metadata["sourceKind"] = "snapshot-captured-source"


def enrich_dotnet_test_discovery(graph: dict, root: Path, rules: dict) -> dict:
    """Capture enabled xUnit/NUnit/MSTest entries using real raw IDs and bounded source reads.

    The caller supplies enabled pinned mod rules. No project code, provider,
    reflection or test runner executes. This additive layer preserves Java
    entries and original nodes/edges; ambiguous declarations are not linked.
    """
    result, provider, frameworks = _merge(graph, rules)
    metadata = result["metadata"]["testDiscovery"]
    if not frameworks:
        metadata["status"] = "partial" if metadata["warnings"] else "complete" if metadata["entries"] else "disabled"
        return result
    inventory, contexts, local, budget = _capture_sources(graph, result, Path(root).resolve(), provider)
    unresolved = _publish_entries(result, frameworks, inventory, contexts, local, provider)
    _finish_metadata(metadata, provider, budget, inventory, contexts, unresolved)
    return result
