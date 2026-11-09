"""Source-only JUnit EnumSource enumeration with exact type and filter resolution."""
from .java_annotations import TYPES, literal_string, location, text


def enum_rows(annotation: dict, record: dict, declarations: list[dict], argument_nodes,
              max_rows: int) -> dict:
    """Resolve one captured enum and INCLUDE/EXCLUDE literal-name filters, never regex execution."""
    unit = annotation["_unit"]
    ast_arguments = annotation["_ast"].child_by_field_name("arguments")
    names = []
    for item in ast_arguments.named_children if ast_arguments else []:
        key = item.child_by_field_name("key") if item.type == "element_value_pair" else None
        names.append(text(key, unit.content) if key else "value")
    if len(names) != len(set(names)) or any(name not in {"value", "names", "mode"} for name in names):
        return {"status": "unresolved", "reason": "EnumSource has duplicate or unsupported options."}
    values = argument_nodes(annotation)
    if len(values) != 1 or values[0].type != "class_literal":
        return {"status": "unresolved", "reason": "EnumSource requires one explicit enum class literal."}
    type_name = text(values[0], unit.content).removesuffix(".class").strip()
    types = {item["qualifiedName"] for item in declarations if item["kind"] in TYPES}
    qualified, _ = unit.resolve(type_name, types, record["owner"])
    matches = [item for item in declarations if item["kind"] == "enum_declaration"
               and item["qualifiedName"] == qualified]
    if len(matches) != 1 or not matches[0].get("nodeId"):
        return {"status": "unresolved", "reason": "Enum type is absent, ambiguous or unbound in the captured graph."}
    declaration = matches[0]
    mode_values = argument_nodes(annotation, "mode")
    mode = text(mode_values[0], unit.content) if len(mode_values) == 1 else "INCLUDE"
    if mode_values and len(mode_values) != 1:
        mode = "unknown"
    # A bare symbol may be a project constant; only the library's enum-qualified spelling is accepted.
    if mode_values and mode not in {"EnumSource.Mode.INCLUDE", "EnumSource.Mode.EXCLUDE",
                                  "org.junit.jupiter.params.provider.EnumSource.Mode.INCLUDE",
                                  "org.junit.jupiter.params.provider.EnumSource.Mode.EXCLUDE"}:
        return {"status": "unresolved", "reason": "EnumSource regex/computed filter modes are not evaluated."}
    mode = mode.rsplit(".", 1)[-1]
    selected = []
    for value in argument_nodes(annotation, "names"):
        name = literal_string(value, unit.content)
        if name is None:
            return {"status": "unresolved", "reason": "EnumSource names contains a computed filter."}
        selected.append(name)
    if len(selected) != len(set(selected)):
        return {"status": "unresolved", "reason": "EnumSource contains duplicate selected names."}
    body = declaration["_ast"].child_by_field_name("body")
    constants = [item for item in body.named_children if item.type == "enum_constant"] if body else []
    available = {text(item.child_by_field_name("name"), declaration["unit"].content) for item in constants}
    if any(name not in available for name in selected):
        return {"status": "unresolved", "reason": "EnumSource refers to a constant absent from the captured enum."}
    rows = []
    count = 0
    for constant in constants:
        name = text(constant.child_by_field_name("name"), declaration["unit"].content)
        included = not selected or name in selected
        if selected and mode == "EXCLUDE":
            included = not included
        if not included:
            continue
        count += 1
        if len(rows) < max_rows:
            expression = f"{type_name}.{name}"
            rows.append({"expression": expression, "values": [expression],
                         "valueFacts": [{"kind": "expression", "expression": expression,
                                         "enumType": qualified, "enumConstant": name}],
                         **location(constant, declaration["file"]),
                         "sourceNodeId": declaration["nodeId"],
                         "sourceHash": declaration["unit"].source_hash})
    return {"status": "declared", "nodeIds": [declaration["nodeId"]], "rows": rows,
            "declaredRowCount": count, "rowsTruncated": count > max_rows,
            "interpretation": "Declared enum constants and literal name filters; no tests are executed."}
