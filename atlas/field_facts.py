"""Captured C# field/property declarations for compact model tables; no runtime inference."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .interface_source import text
from .node_documentation import documentation
from .source_viewer import _read_regular_file, SourceError

_VALUE_TYPES = set("bool byte sbyte short ushort int uint long ulong char float double decimal nint nuint".split())


def project_nullable(root: Path, filename: str) -> tuple[bool | None, str]:
    """Reads only a single nearest project's literal unconditional Nullable setting, without evaluating MSBuild."""
    directory = (root / filename).parent
    while directory.is_relative_to(root):
        projects = sorted(directory.glob("*.csproj"))
        if projects:
            if len(projects) != 1:
                return None, "Ambiguous project"
            try:
                content = _read_regular_file(root, projects[0].relative_to(root), 128 * 1024)
                if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                    return None, "Unsupported project XML"
                tree = ET.fromstring(content)
                settings = []
                for group in tree:
                    for child in group:
                        if child.tag.rsplit("}", 1)[-1] == "Nullable":
                            if group.get("Condition") or child.get("Condition"):
                                return None, "Conditional MSBuild setting"
                            settings.append((child.text or "").strip())
                if len(set(settings)) == 1 and settings[0] in {"enable", "annotations", "disable", "warnings"}:
                    return settings[0] in {"enable", "annotations"}, projects[0].relative_to(root).as_posix()
            except (SourceError, OSError, ET.ParseError):
                pass
            return None, "MSBuild context not evaluated"
        if directory == root:
            break
        directory = directory.parent
    return None, "Project context unavailable"


def field_facts(ast, name: str, content: bytes, filename: str, source_hash: str,
                nullable_context: tuple[bool | None, str], directives: list) -> dict | None:
    """Extracts an exact mapped field/property. Top-level '?' differs from nullable generic arguments."""
    if ast.type not in {"field_declaration", "property_declaration"} or ast.has_error:
        return None
    declaration = ast
    if ast.type == "field_declaration":
        declaration = next((child for child in ast.named_children if child.type == "variable_declaration"), None)
    type_ast = declaration.child_by_field_name("type") if declaration is not None else None
    type_name = text(type_ast, content)
    if not type_name:
        return None
    nullable, evidence = nullable_context
    # AST directives exclude comments and strings. Conditional directives are not evaluated.
    for directive in directives:
        if directive.start_byte >= ast.start_byte:
            break
        if directive.type != "preproc_nullable":
            continue
        setting = text(directive, content).split()
        if len(setting) > 2 and setting[2] == "warnings":
            continue
        parent = directive.parent
        conditional = False
        while parent is not None:
            conditional |= parent.type in {"preproc_if", "preproc_elif", "preproc_else"}
            parent = parent.parent
        mode = setting[1] if len(setting) > 1 else ""
        if conditional:
            nullable, evidence = None, "Conditional #nullable"
        elif mode == "restore":
            nullable, evidence = nullable_context
        elif mode in {"enable", "disable"}:
            nullable, evidence = mode == "enable", f"#nullable {mode}"
    if type_ast.type == "nullable_type" or re.fullmatch(r"(?:System\.)?Nullable\s*<.+>", type_name):
        nullable, evidence = True, "Explicit nullable type"
    elif type_name in _VALUE_TYPES:
        nullable, evidence = False, "C# value type"
    elif nullable is not True:
        nullable = None
    else:
        nullable = False
    doc = documentation(ast, content)
    description = doc["summary"]
    if not description and not doc["unsupported"]:
        description = " ".join(comment["text"].lstrip("/ ").strip() for comment in doc["comments"] if not comment["text"].lstrip().startswith("///"))
    return {"name": name, "type": type_name[:500], "nullable": nullable,
            "nullableEvidence": evidence, "description": description[:2000],
            "kind": ast.type.removesuffix("_declaration"), "file": filename,
            "line": ast.start_point.row + 1, "sourceHash": source_hash,
            "sourceKind": "snapshot-captured-source", "truncated": doc["truncated"] or len(type_name) > 500 or len(description) > 2000,
            "warning": "Declared nullability; runtime values and MSBuild imports/overrides are not evaluated."}
