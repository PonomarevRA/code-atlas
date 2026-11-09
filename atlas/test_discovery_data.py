"""Static parameter-source and resource evidence for Java test discovery."""
from pathlib import PurePosixPath

from .java_annotations import literal_string, location, text, walk
from .test_dataset_csv import csv_fields, csv_options
from .test_dataset_enums import enum_rows
from .test_dataset_values import library_owner, stable_id, value_fact

MAX_ROWS = 30
MAX_PROVIDER_NODES = 12_000


def argument_nodes(annotation: dict, argument: str = "value") -> list:
    """Return source AST values for an explicitly named or positional annotation key."""
    ast = annotation.get("_ast")
    values = ast.child_by_field_name("arguments") if ast else None
    found = []
    for value in values.named_children if values else []:
        if value.type == "element_value_pair":
            key = value.child_by_field_name("key")
            if key is not None and key.text.decode("utf-8") == argument:
                found.append(value.child_by_field_name("value"))
        elif argument == "value":
            found.append(value)
    flattened = []
    for value in found:
        if value is not None and value.type == "element_value_array_initializer":
            flattened.extend(value.named_children)
        elif value is not None:
            flattened.append(value)
    return flattened


def annotation_strings(annotation: dict, content: bytes, argument: str = "value") -> list[str]:
    """Keep literal strings only, never evaluate concatenations or referenced constants."""
    result = []
    for value in argument_nodes(annotation, argument):
        literal = literal_string(value, content)
        if literal is not None:
            result.append(literal)
    return result


def provider_rows(provider: dict) -> dict:
    """Recognize one literal Stream.of(Arguments.of(...)) return, not arbitrary generators."""
    unit, ast = provider["unit"], provider["_ast"]
    body = ast.child_by_field_name("body")
    statements = body.named_children if body else []
    if len(statements) != 1 or statements[0].type != "return_statement":
        return {"status": "unresolved", "reason": "Provider computation is not a single declared Stream.of return."}
    returns = statements[0].named_children
    call = returns[0] if len(returns) == 1 else None
    if call is None or call.type != "method_invocation":
        return {"status": "unresolved", "reason": "Provider return expression is not a static row list."}
    if not library_owner(call.child_by_field_name("object"), unit, "java.util.stream.Stream") or text(call.child_by_field_name("name"), unit.content) != "of":
        return {"status": "unresolved", "reason": "Provider row construction is not recognized."}
    arguments = call.child_by_field_name("arguments")
    rows = []
    for row in arguments.named_children if arguments else []:
        if row.type != "method_invocation" or not library_owner(row.child_by_field_name("object"), unit, "org.junit.jupiter.params.provider.Arguments") or text(row.child_by_field_name("name"), unit.content) != "of":
            return {"status": "unresolved", "reason": "Provider contains computed or spread rows."}
        values = row.child_by_field_name("arguments")
        if len(rows) < MAX_ROWS:
            rows.append({"expression": text(row, unit.content), **location(row, unit.file),
                         "values": [text(value, unit.content, 600) for value in values.named_children] if values else [],
                         "valueFacts": [value_fact(value, unit) for value in values.named_children] if values else [],
                         "sourceNodeId": provider["nodeId"], "sourceHash": unit.source_hash,
                         "expressionTruncated": row.end_byte - row.start_byte > 1600})
    count = len(arguments.named_children) if arguments else 0
    return {"status": "declared", "declaredRowCount": count, "rows": rows,
            "rowsTruncated": count > MAX_ROWS,
            "interpretation": "Declared row expressions; argument values and runtime invocations are not evaluated."}


def _method_sources(annotation: dict, record: dict, declarations: list[dict], argument: str) -> list[dict]:
    """Resolve same-type or explicit FQN#method only when one declaration and raw owner agree."""
    unit = record["unit"]
    references = annotation_strings(annotation, annotation["_unit"].content, argument)
    if not references and not argument_nodes(annotation, argument):
        references = [record["name"]]
    sources = []
    for reference in references[:MAX_ROWS]:
        if "#" in reference:
            owner, name = reference.rsplit("#", 1)
        else:
            owner, name = record["qualifiedName"].rsplit(".", 1)[0], reference
        matches = [item for item in declarations if item["kind"] == "method_declaration"
                   and item["qualifiedName"] == f"{owner}.{name}"]
        source = {"kind": "method-source", "label": reference, "file": annotation["file"],
                  "line": annotation["line"], "nodeIds": [], "expression": annotation["text"],
                  "status": "unresolved", "sourceHash": annotation["sourceHash"]}
        if len(matches) == 1 and matches[0].get("nodeId"):
            provider = matches[0]
            source.update(file=provider["file"], line=provider["line"], nodeIds=[provider["nodeId"]],
                          sourceHash=provider["unit"].source_hash)
            source.update(provider_rows(provider))
        else:
            source["reason"] = "Provider absent, overloaded or not uniquely bound to the captured graph."
        sources.append(source)
    if not sources:
        sources.append({"kind": "method-source", "label": annotation["name"],
                        "file": annotation["file"], "line": annotation["line"], "nodeIds": [],
                        "expression": annotation["text"], "status": "unresolved",
                        "reason": "Provider reference is computed or unsupported."})
    return sources


def _literal_values(annotation: dict, rule: dict, record: dict, declarations: list[dict]) -> list[dict]:
    """Capture each single-value annotation item as one declared row."""
    keys = ("strings", "ints", "longs", "shorts", "bytes", "doubles", "floats", "chars", "booleans", "classes")
    source = _base_source(annotation, "value-source")
    groups = [argument_nodes(annotation, key) for key in keys]
    nonempty = [items for items in groups if items]
    if len(nonempty) != 1:
        source["reason"] = "ValueSource requires exactly one supported literal value array."
        return [source]
    values = nonempty[0]
    if not values:
        source["reason"] = "ValueSource has no supported declared values."
        return [source]
    rows = []
    for value in values[:MAX_ROWS]:
        expression = text(value, annotation["_unit"].content, 600)
        rows.append({"expression": expression, "values": [expression],
                     "valueFacts": [value_fact(value, annotation["_unit"])],
                     "expressionTruncated": value.end_byte - value.start_byte > 600,
                     **location(value, annotation["file"])})
    source.update(status="declared", declaredRowCount=len(values), rows=rows,
                  rowsTruncated=len(values) > MAX_ROWS)
    return [source]


def _base_source(annotation: dict, kind: str) -> dict:
    """Initialize the shared unresolved source shape before a reviewed strategy runs."""
    return {"kind": kind, "label": annotation["name"], "file": annotation["file"],
            "line": annotation["line"], "nodeIds": [], "expression": annotation["text"],
            "status": "unresolved", "sourceHash": annotation["sourceHash"]}


def _csv_sources(annotation: dict, rule: dict, record: dict, declarations: list[dict]) -> list[dict]:
    """Apply only the supported JUnit CSV grammar; unsupported options retain raw evidence."""
    source = _base_source(annotation, "csv-source")
    options, reason = csv_options(annotation, argument_nodes)
    values = argument_nodes(annotation)
    if reason or not values:
        source["reason"] = reason or "CsvSource has no literal rows."
        return [source]
    rows = []
    for value in values:
        literal = literal_string(value, annotation["_unit"].content)
        facts, reason = csv_fields(literal, options) if literal is not None else (None, "CSV row is not a literal string.")
        if reason:
            source["reason"] = reason
            return [source]
        if len(rows) < MAX_ROWS:
            rows.append({"expression": text(value, annotation["_unit"].content, 600),
                         "values": [item["expression"] for item in facts], "valueFacts": facts,
                         "expressionTruncated": value.end_byte - value.start_byte > 600,
                         **location(value, annotation["file"])})
    source.update(status="declared", rows=rows, declaredRowCount=len(values), rowsTruncated=len(values) > MAX_ROWS)
    return [source]


def _enum_sources(annotation: dict, rule: dict, record: dict, declarations: list[dict]) -> list[dict]:
    """Keep enum discovery in the same normalized source contract as literal rows."""
    source = _base_source(annotation, "enum-source")
    source.update(enum_rows(annotation, record, declarations, argument_nodes, MAX_ROWS))
    return [source]


def _provider_sources(annotation: dict, rule: dict, record: dict, declarations: list[dict]) -> list[dict]:
    """Resolve literal method references through the bounded source declaration index."""
    argument = rule.get("sourceArgument") or rule.get("argument") or "value"
    return _method_sources(annotation, record, declarations, argument)


def _unresolved_sources(annotation: dict, rule: dict, record: dict, declarations: list[dict]) -> list[dict]:
    """Represent a declared source whose runtime/library-specific strategy is unsupported."""
    name = str(annotation.get("qualifiedName") or "").rsplit(".", 1)[-1]
    source = _base_source(annotation, name.removesuffix("Source").lower() + "-source")
    source["reason"] = "Source declared; resource loading and arbitrary provider execution are not performed."
    return [source]


# Mods select reviewed, versioned host capabilities; no executable plugin code is loaded.
DATASET_STRATEGIES = {"junit-single-values-v1": _literal_values,
                      "junit-positional-csv-v1": _csv_sources,
                      "junit-literal-provider-v1": _provider_sources,
                      "junit-enum-constants-v1": _enum_sources,
                      "resource-reference-v1": _unresolved_sources,
                      "unresolved-v1": _unresolved_sources}


def data_sources(annotation: dict, rule: dict, record: dict, declarations: list[dict]) -> list[dict]:
    """Dispatch a JSON-selected host strategy and give captured rows stable source identities."""
    strategy = rule.get("datasetStrategy", "unresolved-v1")
    parser = DATASET_STRATEGIES.get(strategy, _unresolved_sources)
    sources = parser(annotation, rule, record, declarations)
    for position, source in enumerate(sources):
        source["id"] = stable_id("test-data:", record["nodeId"], annotation["file"],
                                 annotation["line"], annotation["column"], position)
        source.update(strategy=strategy, composition="declared-rows", runtimeStatus="unknown",
                      sourceKind="snapshot-captured-source")
        source.setdefault("sourceHash", annotation["sourceHash"])
        source["declarationLocation"] = {"file": annotation["file"], "line": annotation["line"],
                                         "sourceHash": annotation["sourceHash"]}
        source["expressionTruncated"] = bool(annotation.get("truncated"))
        source["completeness"] = "partial" if source.get("rowsTruncated") or source["status"] != "declared" or source["expressionTruncated"] else "complete"
        for index, row in enumerate(source.get("rows", [])):
            row.update(id=stable_id("test-data-row:", source["id"], index), index=index,
                       sourceKind="snapshot-captured-source")
            row.setdefault("sourceHash", annotation["sourceHash"])
            if row.get("expressionTruncated") or any(item.get("expressionTruncated") for item in row.get("valueFacts", [])):
                source["completeness"] = "partial"
                row["truncated"] = True
            row["sourceLocation"] = {"file": row["file"], "line": row["line"]}
            if row.get("sourceNodeId"):
                row["sourceLocation"]["nodeId"] = row["sourceNodeId"]
    return sources


def resource_references(record: dict) -> list[dict]:
    """Capture literal repository-resource references in fixture code, never open the data."""
    results = []
    body, unit = record["_ast"].child_by_field_name("body"), record["unit"]
    if body is None:
        return results
    for node in walk(body, MAX_PROVIDER_NODES):
        literal = literal_string(node, unit.content)
        if not literal or not literal.lower().endswith((".sql", ".json", ".csv", ".yaml", ".yml", ".xml")):
            continue
        path = PurePosixPath(literal.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in literal:
            continue
        results.append({"kind": "resource-reference", "label": literal,
                        "file": record["file"], "line": node.start_point.row + 1,
                        "expression": text(node, unit.content), "status": "declared",
                        "interpretation": "Literal reference in fixture source; resource root and execution are not inferred."})
        if len(results) >= MAX_ROWS:
            break
    return results
