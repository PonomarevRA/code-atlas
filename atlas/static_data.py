"""Ограниченные исходные выражения констант и статических словарей, без вычисления значений."""
import re

from .interface_source import text, walk

MAX_ENTRIES = 60
MAX_EXPRESSION_CHARS = 600


def _table_initializer(initializer, source):
    """Допускает только внешний new и его стандартный materializer; аргумент произвольной фабрики не является результатом."""
    current = initializer
    for _ in range(128):
        if current is None:
            return None
        if current.type == "parenthesized_expression" and len(current.named_children) == 1:
            current = current.named_children[0]
            continue
        if current.type == "invocation_expression":
            function = current.child_by_field_name("function")
            if function is None or function.type != "member_access_expression":
                return None
            name = text(function.child_by_field_name("name"), source)
            if name not in {"ToFrozenDictionary", "ToImmutableDictionary", "AsReadOnly"}:
                return None
            arguments = current.child_by_field_name("arguments")
            if arguments and any(child.type in {"lambda_expression", "anonymous_method_expression"} for child in walk(arguments)):
                return None
            current = function.child_by_field_name("expression")
            continue
        if current.type in {"object_creation_expression", "implicit_object_creation_expression"}:
            return next((child for child in current.named_children if child.type == "initializer_expression"), None)
        return None
    return None


def static_field_data(ast, name: str, source: bytes, *, include_instance=False) -> dict | None:
    """Читает выбранный declarator; сохраняет порядок и дубли ключей, не исполняет initializer."""
    if ast.type != "field_declaration" or ast.has_error:
        return None
    modifiers = [text(child, source) for child in ast.named_children if child.type == "modifier"]
    variable = next((child for child in ast.named_children if child.type == "variable_declaration"), None)
    if variable is None:
        return None
    declared_type = text(variable.child_by_field_name("type"), source)
    dictionary = bool(re.search(r"(?:^|\.)\w*Dictionary\s*<", declared_type))
    if "const" not in modifiers and not ((include_instance or "static" in modifiers) and dictionary):
        return None
    declarator = next((child for child in variable.named_children if child.type == "variable_declarator"
                       and text(child.child_by_field_name("name"), source) == name), None)
    if declarator is None:
        return None
    initializer = next((child for child in declarator.named_children if child != declarator.child_by_field_name("name")), None)
    expression = text(initializer, source)
    entries = []
    truncated = len(expression) > MAX_EXPRESSION_CHARS
    if dictionary and initializer is not None:
        # Только initializer dictionary object. Не принимаем вложенные массивы/словарь значения за внешнюю таблицу.
        table = _table_initializer(initializer, source)
        for entry in table.named_children if table else []:
            key = value = None
            if entry.type == "initializer_expression" and len(entry.named_children) == 2:
                key, value = entry.named_children
            elif entry.type == "assignment_expression":
                left = entry.child_by_field_name("left")
                if left is not None and left.type == "element_binding_expression":
                    key = left.named_children[0] if len(left.named_children) == 1 else None
                    value = entry.child_by_field_name("right")
            if key is None or value is None:
                truncated = True
                continue
            if len(entries) == MAX_ENTRIES:
                truncated = True
                break
            key_text, value_text = text(key, source), text(value, source)
            truncated |= max(len(key_text), len(value_text)) > MAX_EXPRESSION_CHARS
            entries.append({"key": key_text[:MAX_EXPRESSION_CHARS], "value": value_text[:MAX_EXPRESSION_CHARS],
                            "line": entry.start_point.row + 1})
    return {"kind": "dictionary" if dictionary else "constant", "initializer": expression[:MAX_EXPRESSION_CHARS],
            "entries": entries, "modifiers": modifiers, "line": ast.start_point.row + 1,
            "sourceKind": "snapshot-captured-source", "truncated": truncated,
            "limits": {"maxEntries": MAX_ENTRIES, "maxExpressionChars": MAX_EXPRESSION_CHARS},
            "warning": "Выражения из объявления, не runtime-значения. Фабрики, nameof, ссылки на константы и изменения словаря не вычисляются."}
