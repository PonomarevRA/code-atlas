"""Краткие пояснения из комментариев объявления, отдельно от авторских заметок."""

import re

from .node_documentation import documentation

COMMENT_FIELDS = ("comment", "commentScope", "implementationComment", "implementationName", "commentSource")
MAX_COMMENT_CHARS = 2000
_TYPES = {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration", "enum_declaration"}


def _summary(doc: dict) -> str:
    """Берёт XML summary или прилегающий обычный комментарий; неподдержанный XML не угадывает."""
    if doc.get("summary"):
        return " ".join(doc["summary"].split())
    if doc.get("unsupported"):
        return ""
    lines = []
    for comment in doc.get("comments", []):
        text = comment.get("text", "").strip()
        if text.startswith("///") and re.search(r"</?[A-Za-z]", text):
            continue
        text = re.sub(r"^/\*+|\*/$", "", text)
        for line in text.splitlines():
            lines.append(re.sub(r"^\s*(?:///|//|\*)\s?", "", line).strip())
    return " ".join(" ".join(lines).split())


def explanatory_comments(doc: dict, context: dict | None, *, source_kind: str,
                         source_hash: str, filename: str, line: int | None) -> dict:
    """Возвращает непустое пояснение и назначение типа с явным источником и пределом 2000 символов.

    Комментарий типа заменяет отсутствующий комментарий метода, но помечается
    containing-type. Пустые поля не добавляются; текст тела метода не используется.
    """
    context = context or {}
    type_doc = context.get("documentation") or {}
    own_text, type_text = _summary(doc), _summary(type_doc)
    if not own_text and not type_text:
        return {}
    selected_doc = doc if own_text else type_doc
    result = {"comment": (own_text or type_text)[:MAX_COMMENT_CHARS],
              "commentScope": "declaration" if own_text else "containing-type"}
    if type_text and type_text != own_text:
        result["implementationComment"] = type_text[:MAX_COMMENT_CHARS]
    if context.get("name"):
        result["implementationName"] = context["name"]
    result["commentSource"] = {
        "sourceKind": source_kind, "sourceHash": source_hash, "file": filename,
        "line": line if own_text else context.get("line"),
        "truncated": bool(selected_doc.get("truncated") or type_doc.get("truncated")
                          or len(own_text) > MAX_COMMENT_CHARS or len(type_text) > MAX_COMMENT_CHARS),
        "unsupported": bool(doc.get("unsupported") or type_doc.get("unsupported")),
    }
    return result


def captured_comments(ast, content: bytes, filename: str, source_hash: str) -> dict:
    """Снимает комментарии точно сопоставленного C# AST, используя уже прочитанные байты файла."""
    if ast.has_error or ast.type == "parameter":
        return {}
    context = ast if ast.type in _TYPES else ast.parent
    while context is not None and context.type not in _TYPES:
        context = context.parent
    type_context = None
    if context is not None:
        name = context.child_by_field_name("name")
        type_context = {"name": name.text.decode("utf-8") if name else "",
                        "line": context.start_point.row + 1,
                        "documentation": documentation(context, content)}
    return explanatory_comments(documentation(ast, content), type_context,
                                source_kind="snapshot-captured-source", source_hash=source_hash,
                                filename=filename, line=ast.start_point.row + 1)
