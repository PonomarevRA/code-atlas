"""Ограниченное описание C#-объявления и безопасный разбор XML-документации."""

import xml.etree.ElementTree as ET

MAX_DOCUMENTATION_CHARS = 8000


def empty_documentation() -> dict:
    """Создаёт независимый пустой объект документации."""
    return {"summary": "", "remarks": "", "returns": "", "params": [], "comments": [],
            "truncated": False, "unsupported": False}


def _text(node, content: bytes) -> str:
    """Читает выбранный AST-диапазон UTF-8."""
    return content[node.start_byte:node.end_byte].decode("utf-8") if node else ""


def _xml_text(node) -> str:
    """Возвращает обычный текст XML без HTML и лишних пробелов."""
    return " ".join(" ".join(node.itertext()).split()) if node is not None else ""


def documentation(declaration, content: bytes) -> dict:
    """Читает прилегающие комментарии; DTD и ENTITY отклоняет до XML-парсера."""
    result = empty_documentation()
    node, next_row = declaration.prev_named_sibling, declaration.start_point.row
    comments, count = [], 0
    while node is not None and node.type == "comment" and node.end_point.row >= next_row - 1:
        text = _text(node, content)
        count += len(text)
        if count > MAX_DOCUMENTATION_CHARS:
            result["truncated"] = True
            break
        comments.append({"text": text, "line": node.start_point.row + 1})
        next_row, node = node.start_point.row, node.prev_named_sibling
    comments.reverse()
    result["comments"] = comments
    xml_lines = []
    for comment in comments:
        for line in comment["text"].splitlines():
            if line.lstrip().startswith("///"):
                xml_lines.append(line.lstrip()[3:])
    if not xml_lines:
        return result
    xml = "\n".join(xml_lines)
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        result["unsupported"] = True
        return result
    try:
        root = ET.fromstring("<documentation>" + xml + "</documentation>")
    except ET.ParseError:
        result["unsupported"] = True
        return result
    for field in ("summary", "remarks", "returns"):
        value = _xml_text(root.find(field))
        result[field] = value[:2000]
        result["truncated"] |= len(value) > 2000
    for parameter in root.findall("param")[:40]:
        name, value = parameter.get("name", ""), _xml_text(parameter)
        result["params"].append({"name": name[:200], "text": value[:2000]})
        result["truncated"] |= len(name) > 200 or len(value) > 2000
    result["truncated"] |= len(root.findall("param")) > 40
    return result


def declaration_facts(declaration, content: bytes) -> dict:
    """Разделяет сигнатуру/документацию объявления и документацию окружающего типа."""
    body = declaration.child_by_field_name("body")
    end = body.start_byte if body is not None else declaration.end_byte
    signature = content[declaration.start_byte:end].decode("utf-8").strip()
    parameters = []
    parameter_list = declaration.child_by_field_name("parameters")
    if parameter_list:
        for parameter in parameter_list.named_children[:40]:
            parameters.append({"name": _text(parameter.child_by_field_name("name"), content)[:200],
                               "type": _text(parameter.child_by_field_name("type"), content)[:300]})
    context = declaration
    while context is not None and context.type not in {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration"}:
        context = context.parent
    type_context = None
    if context is not None:
        type_context = {"name": _text(context.child_by_field_name("name"), content),
                        "kind": context.type.removesuffix("_declaration"), "line": context.start_point.row + 1,
                        "documentation": documentation(context, content)}
    return {"signature": signature[:2000], "parameters": parameters,
            "returnType": _text(declaration.child_by_field_name("returns"), content)[:300],
            "documentation": documentation(declaration, content), "typeContext": type_context,
            "declarationTruncated": len(signature) > 2000 or (parameter_list is not None and len(parameter_list.named_children) > 40)}
