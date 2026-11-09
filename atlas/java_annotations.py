"""Bounded Java declarations and annotation occurrences, without loading project code."""
from dataclasses import dataclass
import hashlib
import json
import re

MAX_AST_NODES = 100_000
MAX_DECLARATIONS = 4_000
MAX_ANNOTATIONS = 80
MAX_ARGUMENTS = 40
MAX_TEXT = 1_600
TYPES = {"class_declaration", "interface_declaration", "enum_declaration",
         "record_declaration", "annotation_type_declaration"}
METHODS = {"method_declaration", "constructor_declaration"}


def text(node, content: bytes, limit: int = MAX_TEXT) -> str:
    """Read a bounded source slice; byte coordinates belong to the parsed file."""
    return content[node.start_byte:node.end_byte].decode("utf-8")[:limit] if node else ""


def location(node, filename: str) -> dict:
    """Keep one-based source coordinates for an occurrence or declaration."""
    return {"file": filename, "line": node.start_point.row + 1,
            "endLine": node.end_point.row + 1, "column": node.start_point.column + 1}


def walk(node, limit: int = MAX_AST_NODES):
    """Iterate named syntax in source order under a fixed inspection budget."""
    pending, count = [node], 0
    while pending and count < limit:
        item = pending.pop()
        count += 1
        yield item
        pending.extend(reversed(item.named_children))


def literal_string(node, content: bytes) -> str | None:
    """Decode only a Java string literal; expressions and constants stay unknown."""
    if node is None or node.type != "string_literal":
        return None
    raw = text(node, content)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


@dataclass
class JavaUnit:
    """One already-read source and its bounded, non-executable declaration index."""
    file: str
    content: bytes
    package: str
    imports: dict
    wildcards: list
    declarations: list
    truncated: bool
    parse_errors: bool
    source_hash: str
    inspected_nodes: int
    value_names: set[str]

    def resolve(self, name: str, known_names: set[str], owner: str = "") -> tuple[str | None, str]:
        """Resolve explicit imports/package or an unambiguous known wildcard name."""
        # Enclosing member types shadow imports in Java. Do this before import lookup;
        # otherwise a project-local @interface Test becomes a false JUnit entry.
        if "." not in name:
            parts = owner.split(".") if owner else []
            while parts:
                candidate = ".".join(part for part in (self.package, ".".join(parts), name) if part)
                if candidate in known_names:
                    return candidate, "enclosing-type"
                parts.pop()
        if "." in name:
            head, tail = name.split(".", 1)
            if head in self.imports:
                return (self.imports[head] + "." + tail, "import") if self.imports[head] else (None, "ambiguous")
            if name[0].islower():
                return name, "qualified"
        if name in self.imports:
            return self.imports[name], "import" if self.imports[name] else "ambiguous"
        local = f"{self.package}.{name}" if self.package else name
        if local in known_names:
            return local, "package"
        candidates = [f"{prefix}.{name}" for prefix in self.wildcards
                      if f"{prefix}.{name}" in known_names]
        if len(candidates) == 1:
            return candidates[0], "wildcard-candidate"
        return None, "ambiguous" if candidates else "unresolved"


def _annotations(declaration, unit: JavaUnit, known_names: set[str], owner: str = "") -> list[dict]:
    """Capture only direct modifiers; comments and nested annotations are not siblings."""
    modifiers = next((item for item in declaration.named_children if item.type == "modifiers"), None)
    annotations = []
    for node in modifiers.named_children if modifiers else []:
        if node.type not in {"annotation", "marker_annotation"}:
            continue
        if len(annotations) >= MAX_ANNOTATIONS:
            unit.truncated = True
            break
        name = text(node.child_by_field_name("name"), unit.content)
        qualified, resolution = unit.resolve(name, known_names, owner)
        arguments, shortened = [], node.end_byte - node.start_byte > MAX_TEXT
        values = node.child_by_field_name("arguments")
        for value in values.named_children if values else []:
            if len(arguments) >= MAX_ARGUMENTS:
                shortened = True
                break
            key = value.child_by_field_name("key") if value.type == "element_value_pair" else None
            expression = value.child_by_field_name("value") if key else value
            arguments.append({"position": len(arguments) + 1, "name": text(key, unit.content) or None,
                              "nameKind": "property" if key else None,
                              "expression": text(expression, unit.content),
                              **location(value, unit.file),
                              "truncated": expression.end_byte - expression.start_byte > MAX_TEXT})
        annotations.append({"name": name, "qualifiedName": qualified,
                            "resolution": resolution, "target": None,
                            "arguments": arguments, "argumentsTruncated": shortened,
                            "text": text(node, unit.content), **location(node, unit.file),
                            "truncated": shortened or node.has_error,
                            "sourceKind": "snapshot-captured-source", "sourceHash": unit.source_hash,
                            "_ast": node, "_unit": unit})
    return annotations


def parse_java(content: bytes, filename: str, known_names: set[str], max_ast_nodes: int = MAX_AST_NODES) -> JavaUnit:
    """Index types, methods and parameter annotations with hard AST/declaration bounds.

    The caller owns safe filesystem reads. No classpath, annotation processor,
    reflection, build script or provider is evaluated here.
    """
    from tree_sitter import Language, Parser
    import tree_sitter_java

    root = Parser(Language(tree_sitter_java.language())).parse(content).root_node
    package, imports, wildcards = "", {}, []
    for child in root.named_children:
        if child.type == "package_declaration":
            package = text(child, content).removeprefix("package").rstrip(";").strip()
        elif child.type == "import_declaration":
            value = text(child, content).removeprefix("import").rstrip(";").strip()
            if value.startswith("static "):
                continue
            if value.endswith(".*"):
                wildcards.append(value[:-2])
            else:
                short_name = value.rsplit(".", 1)[-1]
                previous = imports.get(short_name, value)
                imports[short_name] = value if previous == value else None
    unit = JavaUnit(filename, content, package, imports, wildcards, [], False, root.has_error,
                    hashlib.sha256(content).hexdigest(), 0, set())
    nodes = list(walk(root, max_ast_nodes))
    # Capitalized variables may shadow imported types in qualified method calls.
    for item in nodes:
        if item.type in {"variable_declarator", "formal_parameter", "spread_parameter", "catch_formal_parameter"}:
            name = text(item.child_by_field_name("name"), content)
            if name:
                unit.value_names.add(name)
    unit.inspected_nodes = len(nodes)
    unit.truncated = len(nodes) >= max_ast_nodes
    local_types = set(known_names)
    for node in nodes:
        if node.type in TYPES:
            name = text(node.child_by_field_name("name"), content)
            parents, ancestor = [], node.parent
            while ancestor is not None:
                if ancestor.type in TYPES:
                    parents.append(text(ancestor.child_by_field_name("name"), content))
                ancestor = ancestor.parent
            local_types.add(".".join(part for part in (package, ".".join(reversed(parents)), name) if part))
    for node in nodes:
        if node.type not in TYPES | METHODS:
            continue
        if len(unit.declarations) >= MAX_DECLARATIONS:
            unit.truncated = True
            break
        name = text(node.child_by_field_name("name"), content)
        parents, ancestor = [], node.parent
        while ancestor is not None:
            if ancestor.type in TYPES:
                parents.append(text(ancestor.child_by_field_name("name"), content))
            ancestor = ancestor.parent
        owner = ".".join(reversed(parents))
        qualified = ".".join(part for part in (package, owner, name) if part)
        superclass = node.child_by_field_name("superclass")
        base = text(superclass, content).removeprefix("extends").strip()
        record = {"name": name, "owner": owner, "qualifiedName": qualified,
                  "kind": node.type, "base": base, **location(node, filename),
                  "annotations": _annotations(node, unit, local_types, owner),
                  "parameters": [], "nodeId": None, "_ast": node, "unit": unit}
        parameters = node.child_by_field_name("parameters")
        for parameter in parameters.named_children if parameters else []:
            # A Java receiver (`Case this`) describes the owner, not an argument
            # supplied by a test runner. Number only actual value parameters.
            if parameter.type not in {"formal_parameter", "spread_parameter"}:
                continue
            position = len(record["parameters"]) + 1
            record["parameters"].append({"name": text(parameter.child_by_field_name("name"), content),
                                         "position": position, "type": text(parameter.child_by_field_name("type"), content),
                                         **location(parameter, filename),
                                         "annotations": _annotations(parameter, unit, local_types, owner)})
        unit.declarations.append(record)
    return unit


def public_annotation(annotation: dict) -> dict:
    """Drop internal AST handles before a fact enters a saved JSON snapshot."""
    return {key: value for key, value in annotation.items() if not key.startswith("_")}


def attribute_facts(record: dict, containing: list[dict]) -> dict:
    """Use the common scoped attribute contract; containing types remain distinct."""
    groups = []
    for scope, owner in [("declaration", record), *[("type", item) for item in containing]]:
        groups.append({"scope": scope, "name": owner["name"], "kind": owner["kind"],
                       "file": owner["file"], "line": owner["line"], "endLine": owner["endLine"],
                       "column": owner["column"],
                       "attributes": [public_annotation(item) for item in owner["annotations"]]})
    for parameter in record["parameters"]:
        if parameter["annotations"]:
            groups.append({"scope": "parameter", "name": parameter["name"], "kind": "formal_parameter",
                           "file": parameter["file"], "line": parameter["line"],
                           "column": parameter["column"], "position": parameter["position"],
                           "attributes": [public_annotation(item) for item in parameter["annotations"]]})
    return {"kind": "lexical-source", "status": "partial" if record["unit"].truncated else "parsed",
            "language": "java", "groups": groups, "truncated": record["unit"].truncated,
            "sourceKind": "snapshot-captured-source", "sourceHash": record["unit"].source_hash,
            "warning": "Java annotations are lexical source facts; values and callbacks are not executed."}


def bind_raw_declarations(unit: JavaUnit, raw_nodes: list[dict]) -> None:
    """Bind exact file/name/line ranges once; collisions remain deliberately unresolved."""
    reverse = {}
    for declaration in unit.declarations:
        candidates = []
        for node in raw_nodes:
            label = str((node.get("memberFacts") or {}).get("name") or node.get("label") or node.get("name") or "")
            name = label.lstrip(".").split("(", 1)[0]
            line = node.get("line")
            if not isinstance(line, int) or isinstance(line, bool):
                found = re.match(r"^L?(\d+)", str(node.get("source_location") or ""))
                line = int(found[1]) if found else None
            if name != declaration["name"] or line is None:
                continue
            if not declaration["line"] <= line <= declaration["endLine"]:
                continue
            method = declaration["kind"] in METHODS
            if method and node.get("kind") in {"class", "file", "interface", "field", "enum"}:
                continue
            if not method and ("(" in label or node.get("kind") in {"method", "field", "file"}):
                continue
            candidates.append(node["id"])
        if len(candidates) == 1:
            declaration["nodeId"] = candidates[0]
            reverse.setdefault(candidates[0], []).append(declaration)
    for declarations in reverse.values():
        if len(declarations) > 1:
            for declaration in declarations:
                declaration["nodeId"] = None
