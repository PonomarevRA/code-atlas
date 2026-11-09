"""Ограниченный анализ рабочего или чернового текста без изменения файлов проекта."""

from copy import deepcopy
from collections import OrderedDict, defaultdict
import hashlib
import importlib
from pathlib import Path
import re
import threading

from .code_complexity import analyze_complexity
from .code_health import capture_health_facts
from .member_languages import LANGUAGES, MemberLanguage

CSHARP = MemberLanguage("csharp", "tree_sitter_c_sharp", frozenset({"class_declaration", "interface_declaration", "struct_declaration", "record_declaration"}),
                       frozenset({"method_declaration", "constructor_declaration", "local_function_statement"}), frozenset())
DEFERRED = {"lambda_expression", "lambda", "arrow_function", "function_expression", "func_literal",
            "closure_expression", "async_block", "const_block"}
SUPPORTED = {"csharp", "python", "java", "javascript", "typescript", "go", "rust"}
MAX_AST = 200000
_stamps = OrderedDict()
_stamp_lock = threading.RLock()


def source_stamp(declaration, content: bytes, language: str, source_hash: str) -> str:
    """Комментарий не меняет dependency stamp; строки и все executable tokens сохраняются."""
    key = (language, source_hash)
    with _stamp_lock:
        if key in _stamps:
            _stamps.move_to_end(key)
            return _stamps[key]
    root = declaration
    while root.parent:
        root = root.parent
    pending, digest = [root], hashlib.sha256()
    visited = 0
    while pending and visited < 1500000:
        node = pending.pop()
        visited += 1
        if "comment" in node.type:
            continue
        if language == "python" and node.type == "expression_statement" and node.named_children and node.named_children[0].type == "string":
            parent = node.parent
            if parent and parent.named_children and parent.named_children[0] == node and (parent.type == "module" or parent.parent and parent.parent.type in {"function_definition", "class_definition"}):
                continue
        if node.children:
            pending.extend(reversed(node.children))
        else:
            digest.update(content[node.start_byte:node.end_byte]); digest.update(b"\0")
    value = digest.hexdigest() if not pending else ""
    with _stamp_lock:
        _stamps[key] = value
        while len(_stamps) > 16:
            _stamps.popitem(last=False)
    return value


def language_for(filename: str):
    """Определяет только поставляемую грамматику по расширению, без импорта кода проекта."""
    suffix = Path(filename).suffix.lower()
    language = CSHARP if suffix == ".cs" else LANGUAGES.get(suffix)
    return language if language and language.name in SUPPORTED else None


def parse_health_source(filename: str, content: bytes, *, source_kind: str = "working-tree", max_methods: int = 120) -> dict:
    """Возвращает самостоятельные методы и deferred тела; ошибки и бюджеты остаются явными."""
    from tree_sitter import Language, Parser

    language = language_for(filename)
    digest = hashlib.sha256(content).hexdigest()
    result = {"file": filename, "sourceHash": digest, "methods": [], "truncated": False, "warnings": [], "status": "complete"}
    if not language:
        result["status"] = "unavailable"
        result["warnings"].append("Language documentation/health adapter unavailable")
        return result
    try:
        module = importlib.import_module(language.module)
        parser = Parser(Language(getattr(module, language.language_function)()))
        tree = parser.parse(content)
    except (ImportError, AttributeError, ValueError) as error:
        result["status"] = "unavailable"
        result["warnings"].append("Grammar unavailable: " + str(error)[:200])
        return result
    pending, visited = [tree.root_node], 0
    while pending and visited < MAX_AST:
        ast = pending.pop()
        visited += 1
        if ast.type in language.methods or ast.type in DEFERRED:
            if len(result["methods"]) >= max_methods:
                result["truncated"] = True
                break
            named = ast.child_by_field_name("name")
            named_function = ast.type in {"arrow_function", "function_expression"} and ast.parent and ast.parent.type == "variable_declarator"
            if named_function:
                named = ast.parent.child_by_field_name("name")
            name = content[named.start_byte:named.end_byte].decode("utf-8") if named else "<" + ast.type + ">"
            kind = "deferred" if ast.type in DEFERRED and not named_function else "constructor" if ast.type == "constructor_declaration" or name in {"__init__", "constructor"} else "method"
            node = {"id": "", "label": name + "()", "file": filename, "line": ast.start_point.row + 1,
                    "endLine": ast.end_point.row + 1, "kind": kind, "language": language.name,
                    "name": name, "parameterList": re.sub(r"\s+", "", content[ast.child_by_field_name("parameters").start_byte:ast.child_by_field_name("parameters").end_byte].decode("utf-8"))
                        if ast.child_by_field_name("parameters") else ""}
            node["complexity"] = analyze_complexity(ast, content, language.name, source_kind=source_kind, source_hash=digest)
            capture_health_facts(node, ast, content, language.name, filename=filename, source_hash=digest, source_kind=source_kind)
            if any(parent.has_error for parent in _owners(ast, language.classes)):
                node["complexity"]["status"] = "unavailable"
                node["complexity"]["score"] = None
                node["documentationHealth"].update(status="unavailable", score=None, documented=None)
            node["id"] = "health-source:" + node["healthIdentity"]
            # Snapshot, working-tree and draft all consume the same bounded facts.
            call_facts = node["healthCallFacts"]
            node["lexicalCalls"] = [call["expression"] for call in call_facts["calls"]]
            node["callsTruncated"] = call_facts["status"] != "complete"
            node["lexicalWrites"] = call_facts["lexicalWrites"]
            node["writesUnknown"] = call_facts["writesUnknown"]
            result["methods"].append(node)
        pending.extend(reversed(ast.named_children))
    result["truncated"] |= bool(pending)
    if tree.root_node.has_error:
        result["status"] = "partial"
        result["warnings"].append("Source contains syntax errors; recovery declarations are not trusted")
    if result["truncated"]:
        result["status"] = "partial"
    if not result["methods"]:
        result["warnings"].append("No assessable declarations in the selected file")
    return result


def bind_source_methods(methods: list[dict], graph: dict) -> list[dict]:
    """Совпадение identity приоритетно; legacy имена используются лишь без коллизий."""
    records = deepcopy(methods)
    identities = defaultdict(list)
    for item in graph.get("nodes", []):
        if item.get("healthIdentity"):
            identities[item["healthIdentity"]].append(item)
    by_identity = {key: items[0] for key, items in identities.items() if len(items) == 1}
    used = set()
    for node in records:
        match = by_identity.get(node["healthIdentity"])
        if match is None and node["kind"] != "deferred":
            candidates = []
            for raw in graph.get("nodes", []):
                if raw.get("file") != node["file"] or raw.get("_callable_class") or raw.get("kind") in {"field", "property", "parameter", "class", "interface", "type"}:
                    continue
                name = (raw.get("memberFacts") or {}).get("name") or re.split(r"[.(]", str(raw.get("label", "")))[-1]
                if not (raw.get("memberFacts") or {}).get("name"):
                    label = str(raw.get("label", "")).split("(")[0]
                    name = label.rsplit(".", 1)[-1]
                if name == node["name"] and (raw.get("complexity") or raw.get("_callable") or "(" in str(raw.get("label", ""))):
                    candidates.append(raw)
            if len(candidates) == 1:
                match = candidates[0]
        if match and match["id"] not in used:
            if match.get("memberFacts"):
                node["memberFacts"] = deepcopy(match["memberFacts"])
            node["rawNodeId"] = match["id"]
            node["id"] = match["id"]
            used.add(match["id"])
    return records


def _owners(declaration, classes):
    """Невалидный enclosing class не превращается в надёжное объявление метода."""
    parent = declaration.parent
    while parent:
        if parent.type in classes:
            yield parent
        parent = parent.parent
