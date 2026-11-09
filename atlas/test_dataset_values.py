"""Bounded literal value facts for declared Java datasets; project code is never run."""
import hashlib
import json
import re

from .java_annotations import TYPES, literal_string, text

MAX_MAP_ENTRIES = 30


def stable_id(prefix: str, *parts) -> str:
    """Derive snapshot-stable identities from source ownership and declaration coordinates."""
    key = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return prefix + hashlib.sha256(key.encode()).hexdigest()[:24]


def java_character(node, content: bytes) -> str | None:
    """Decode the supported Java character literal subset without evaluating expressions."""
    raw = text(node, content)
    if node is None or node.type != "character_literal" or len(raw) < 3:
        return None
    value = raw[1:-1]
    if len(value) == 1:
        return value
    escapes = {r"\t": "\t", r"\n": "\n", r"\r": "\r", r"\b": "\b", r"\f": "\f",
               r"\'": "'", r'\"': '"', r"\\": "\\"}
    if value in escapes:
        return escapes[value]
    if re.fullmatch(r"\\u[0-9a-fA-F]{4}", value):
        return chr(int(value[2:], 16))
    return None


def library_owner(node, unit, expected: str) -> bool:
    """Accept only an exact imported/qualified library type, rejecting local type shadows."""
    value = text(node, unit.content)
    if unit.truncated or value.split(".", 1)[0] in unit.value_names:
        return False
    declarations = [item for item in unit.declarations if item["kind"] in TYPES]
    known = {item["qualifiedName"] for item in declarations}
    known.add(expected)
    owner = ""
    if node is not None:
        containing = [item for item in declarations
                      if item["_ast"].start_byte <= node.start_byte <= item["_ast"].end_byte]
        if containing:
            closest = min(containing, key=lambda item: item["_ast"].end_byte - item["_ast"].start_byte)
            owner = ".".join(part for part in (closest["owner"], closest["name"]) if part)
    if "." not in value and any(item["name"] == value and not item["owner"] for item in declarations):
        return False
    qualified, _ = unit.resolve(value, known, owner)
    return qualified == expected


def value_fact(node, unit, depth: int = 0) -> dict:
    """Describe literals and a small Map.of shape, keeping computed values as expressions."""
    expression = text(node, unit.content, 600)
    base = {"kind": "expression", "expression": expression}
    if node is not None and node.end_byte - node.start_byte > 600:
        return {**base, "expressionTruncated": True,
                "reason": "Value expression exceeds the captured character budget."}
    if node is None:
        return {"kind": "missing", "expression": ""}
    literal = literal_string(node, unit.content)
    if literal is not None:
        return {"kind": "string", "expression": expression, "value": literal}
    char = java_character(node, unit.content)
    if char is not None:
        return {"kind": "string", "expression": expression, "value": char}
    if node.type == "null_literal":
        return {"kind": "null", "expression": expression, "value": None}
    if node.type in {"true", "false", "boolean_literal"} and expression in {"true", "false"}:
        return {"kind": "boolean", "expression": expression, "value": expression == "true"}
    # Keep exact source numerals instead of coercing Java widths, suffixes or precision.
    if node.type.endswith("integer_literal") or node.type.endswith("floating_point_literal"):
        return {"kind": "number", "expression": expression, "value": expression}
    if node.type == "unary_expression" and re.fullmatch(r"[-+]\d+[lLfFdD]?", expression):
        return {"kind": "number", "expression": expression, "value": expression}
    if depth >= 3 or node.type != "method_invocation":
        return base
    name = text(node.child_by_field_name("name"), unit.content)
    if name != "of" or not library_owner(node.child_by_field_name("object"), unit, "java.util.Map"):
        return base
    arguments = node.child_by_field_name("arguments")
    values = arguments.named_children if arguments else []
    if len(values) % 2 or len(values) > MAX_MAP_ENTRIES * 2:
        return {**base, "reason": "Map literal exceeds the supported key/value shape."}
    entries, keys = [], set()
    for index in range(0, len(values), 2):
        key = value_fact(values[index], unit, depth + 1)
        value = value_fact(values[index + 1], unit, depth + 1)
        if values[index].type != "string_literal" or key["kind"] != "string" or value["kind"] == "null":
            return {**base, "reason": "Map.of keys are not unique known literals or a value is null."}
        identity = (key["kind"], key.get("value"))
        if identity in keys:
            return {**base, "reason": "Map.of has duplicate literal keys; runtime success is unknown."}
        keys.add(identity)
        entries.append({"key": key, "value": value})
    return {"kind": "map", "expression": expression, "entries": entries,
            "completeness": "complete", "interpretation": "Literal keys only; no provider execution."}
