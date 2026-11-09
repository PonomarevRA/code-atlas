"""Ограниченные AST-факты параметров и аргументов C# без семантического dataflow."""

import hashlib

MAX_PARAMETERS = 40
MAX_ARGUMENTS = 40
MAX_CONDITIONS = 100
MAX_EXPRESSION_CHARS = 600
MAX_REFERENCE_NODES = 2000
_NESTED_SCOPES = {"method_declaration", "constructor_declaration", "local_function_statement",
                  "class_declaration", "lambda_expression", "anonymous_method_expression"}
_MODIFIERS = {"this", "ref", "out", "in", "params", "scoped", "readonly"}
_CONTROL_FIELDS = {
    "if_statement": ("if", (("condition", "condition"),)),
    "conditional_expression": ("conditional", (("condition", "condition"),)),
    "switch_statement": ("switch", (("value", "selector"),)),
    "foreach_statement": ("foreach", (("right", "collection"),)),
    "for_statement": ("for", (("initializer", "initializer"), ("condition", "condition"), ("update", "update"))),
    "while_statement": ("while", (("condition", "condition"),)),
    "do_statement": ("do", (("condition", "condition"),)),
}
FACTS_WARNING = ("Показаны выражения и совпадения имён формальных параметров в текущем C#-исходнике. "
                 "Значения, влияние через промежуточные переменные, достижимость, фактические ветви "
                 "и привязка аргументов к перегрузке не вычисляются. Тела вложенных методов "
                 "и лямбда-выражений исключены из совпадений.")


def empty_parameter_facts() -> dict:
    """Возвращает независимую пустую модель с явной границей доказательств и лимитами."""
    return {"kind": "lexical-source", "parameters": [], "conditions": [], "truncated": False,
            "limits": {"maxParameters": MAX_PARAMETERS, "maxArgumentsPerCall": MAX_ARGUMENTS,
                       "maxConditions": MAX_CONDITIONS, "maxExpressionChars": MAX_EXPRESSION_CHARS,
                       "maxReferenceNodes": MAX_REFERENCE_NODES}, "warning": FACTS_WARNING}


def _text(node, content: bytes) -> str:
    """Читает точный AST-диапазон; обрезка применяется только к сериализованным фактам."""
    return content[node.start_byte:node.end_byte].decode("utf-8") if node is not None else ""


def _segments(parameter_list) -> list[list]:
    """Делит список по внешним запятым, учитывая плоский AST последнего params-параметра."""
    segments, current = [], []
    for index, child in enumerate(parameter_list.children):
        if child.type in {"(", ")"}:
            continue
        if child.type == ",":
            if current:
                segments.append(current)
                current = []
            continue
        if child.type == "parameter":
            current.extend((child.field_name_for_child(part), item) for part, item in enumerate(child.children))
        else:
            current.append((parameter_list.field_name_for_child(index), child))
    if current:
        segments.append(current)
    return segments


def formal_parameters(declaration, content: bytes) -> tuple[list[dict], bool]:
    """Сохраняет имена, типы, модификаторы и буквальное выражение default выбранного объявления."""
    parameter_list = declaration.child_by_field_name("parameters")
    if parameter_list is None:
        return [], False
    segments = _segments(parameter_list)
    parameters = []
    truncated = len(segments) > MAX_PARAMETERS
    for segment in segments[:MAX_PARAMETERS]:
        name = next((child for field, child in segment if field == "name"), None)
        parameter_type = next((child for field, child in segment if field == "type"), None)
        if name is None:
            truncated = True
            continue
        modifiers = []
        default = None
        after_equals = False
        for _, child in segment:
            text = _text(child, content)
            if child.type == "modifier" or text in _MODIFIERS:
                modifiers.append(text)
            if child.type == "=":
                after_equals = True
                continue
            if after_equals and child.is_named and child.type != "comment":
                default = text
        name_text, type_text = _text(name, content), _text(parameter_type, content)
        shortened = len(name_text) > 200 or len(type_text) > 300 or len(default or "") > MAX_EXPRESSION_CHARS
        truncated |= shortened
        parameters.append({"name": name_text.removeprefix("@")[:200], "type": type_text[:300],
                           "line": name.start_point.row + 1, "modifier": " ".join(modifiers) or None,
                           "defaultValue": default[:MAX_EXPRESSION_CHARS] if default is not None else None,
                           "truncated": shortened})
    return parameters, truncated


def _parameter_references(expression, content: bytes, names: set[str]) -> tuple[list[str], bool]:
    """Находит только AST-идентификаторы, исключая имена членов, типов и вложенные области видимости."""
    if expression is None or not names:
        return [], False
    pending, found, scanned = [expression], set(), 0
    while pending:
        node = pending.pop()
        scanned += 1
        if scanned > MAX_REFERENCE_NODES:
            return sorted(found), True
        if node.type in _NESTED_SCOPES:
            continue
        if node.type == "identifier":
            name = _text(node, content).removeprefix("@")
            parent = node.parent
            is_name = parent is not None and parent.child_by_field_name("name") == node
            is_type = parent is not None and parent.child_by_field_name("type") == node
            if name in names and not is_name and not is_type:
                found.add(name)
        pending.extend(reversed(node.named_children))
    return sorted(found), False


def invocation_arguments(invocation, content: bytes, parameter_names: set[str]) -> dict:
    """Передаёт исходные argument expressions, не связывая позиции с формальными параметрами callee."""
    current = invocation.parent
    while current is not None and current.type not in {"method_declaration", "constructor_declaration", "local_function_statement"}:
        if current.type in {"lambda_expression", "anonymous_method_expression"}:
            parameter_names = set()
            break
        current = current.parent
    argument_list = invocation.child_by_field_name("arguments")
    argument_nodes = [child for child in argument_list.named_children if child.type == "argument"] if argument_list else []
    arguments, truncated = [], len(argument_nodes) > MAX_ARGUMENTS
    for position, argument in enumerate(argument_nodes[:MAX_ARGUMENTS], 1):
        name_node = argument.child_by_field_name("name")
        expressions = [child for child in argument.named_children if child != name_node and child.type != "comment"]
        expression = expressions[-1] if expressions else None
        text = _text(expression, content)
        references, limited = _parameter_references(expression, content, parameter_names)
        modifiers = [child.type for child in argument.children if child.type in {"ref", "out", "in"}]
        shortened = limited or len(text) > MAX_EXPRESSION_CHARS
        arguments.append({"position": position, "name": _text(name_node, content) or None,
                          "modifier": " ".join(modifiers) or None, "expression": text[:MAX_EXPRESSION_CHARS],
                          "parameterReferences": references, "line": argument.start_point.row + 1,
                          "truncated": shortened})
        truncated |= shortened
    return {"arguments": arguments, "argumentsTruncated": truncated, "argumentKind": "lexical-source"}


def parameter_facts(declaration, content: bytes, node_id: str) -> dict:
    """Показывает только условия/заголовки циклов, прямо содержащие имя параметра выбранного метода."""
    result = empty_parameter_facts()
    result["parameters"], result["truncated"] = formal_parameters(declaration, content)
    names = {parameter["name"].removeprefix("@") for parameter in result["parameters"]}
    pending = [declaration]
    while pending:
        node = pending.pop()
        if node != declaration and node.type in _NESTED_SCOPES:
            continue
        control = _CONTROL_FIELDS.get(node.type)
        expressions = []
        if control is not None:
            kind, fields = control
            for field, role in fields:
                expression = node.child_by_field_name(field)
                if expression is not None:
                    expressions.append((kind, role, expression))
        elif node.type == "switch_expression" and node.named_children:
            expressions.append(("switch-expression", "selector", node.named_children[0]))
        elif node.type == "when_clause":
            expressions.append(("when", "guard", node))
        for kind, role, expression in expressions:
            references, limited = _parameter_references(expression, content, names)
            result["truncated"] |= limited
            if not references:
                continue
            if len(result["conditions"]) >= MAX_CONDITIONS:
                result["truncated"] = True
                return result
            text = _text(expression, content)
            digest = hashlib.sha256(f"{node_id}:{node.start_byte}:{kind}".encode()).hexdigest()[:16]
            shortened = limited or len(text) > MAX_EXPRESSION_CHARS
            result["conditions"].append({"kind": kind, "headerRole": role,
                                         "line": node.start_point.row + 1, "column": node.start_point.column + 1,
                                         "expression": text[:MAX_EXPRESSION_CHARS], "parameterReferences": references,
                                         "branchId": "branch-" + digest if kind != "when" else None,
                                         "truncated": shortened})
            result["truncated"] |= shortened
        pending.extend(reversed(node.named_children))
    return result
