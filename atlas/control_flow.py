"""Лексические ветви C#; условия и достижимость никогда не вычисляются."""

import hashlib


MAX_BRANCHES = 100
MAX_ARMS = 240
MAX_TERMINALS = 160
_SKIPPED = {"method_declaration", "constructor_declaration", "local_function_statement", "class_declaration"}
_TERMINALS = {"return_statement": "return", "throw_statement": "throw", "throw_expression": "throw",
              "break_statement": "break", "continue_statement": "continue"}


def _text(node, content: bytes) -> str:
    """Возвращает ограниченный текст доказательства, не интерпретируя выражение."""
    return content[node.start_byte:node.end_byte].decode("utf-8")[:1000] if node else ""


def _loop_header(node, content: bytes) -> str:
    """Возвращает заголовок for/foreach до тела, включая коллекцию и обновление счётчика."""
    if node.type not in {"for_statement", "foreach_statement"}:
        return ""
    body = node.child_by_field_name("body")
    if body is None:
        return ""
    return content[node.start_byte:body.start_byte].decode("utf-8").strip()[:1000]


def _point(node, end: bool = False) -> tuple[int, int]:
    """Использует байтовую колонку tree-sitter, как события execution path."""
    point = node.end_point if end else node.start_point
    return point.row + 1, point.column + 1


def _arm(branch_id: str, index: int, kind: str, label: str, node, body,
         condition: str | None = None, labels: list | None = None) -> tuple[dict, tuple | None]:
    """Создаёт описательную ветвь и отдельный точный диапазон её исполняемого тела."""
    identifier = f"{branch_id}-a{index}"
    result = {"id": identifier, "kind": kind, "label": label[:1000], "condition": condition,
              "line": node.start_point.row + 1, "endLine": node.end_point.row + 1,
              "labels": labels or []}
    span = (identifier, _point(body), _point(body, True)) if body is not None else None
    return result, span


def _switch_arms(node, content: bytes, identifier: str) -> list:
    """Объединяет соседние case-метки общего тела; guard остаётся у своей метки."""
    body = node.child_by_field_name("body")
    sections = [child for child in body.named_children if child.type == "switch_section"] if body else []
    result, pending = [], []
    first = None
    for section in sections:
        colon = next((child for child in section.children if child.type == ":"), None)
        if colon is None:
            continue
        guard = next((child for child in section.named_children if child.type == "when_clause"), None)
        header = content[section.start_byte:colon.start_byte].decode("utf-8")[:1000]
        pending.append({"text": header, "guard": _text(guard, content) or None})
        first = first or section
        statements = [child for child in section.named_children if child.start_byte >= colon.end_byte and child.type != "comment"]
        # Тело начинается сразу после ':'; комментарии внутри него тоже принадлежат arm.
        body_nodes = [child for child in section.named_children if child.start_byte >= colon.end_byte]
        if not statements and section != sections[-1]:
            continue
        label = " / ".join(item["text"] for item in pending)
        kind = "default" if any(item["text"].strip() == "default" for item in pending) else "case"
        arm, _ = _arm(identifier, len(result), kind, label, first, None,
                      pending[0]["guard"] if len(pending) == 1 else None, list(pending))
        arm["endLine"] = section.end_point.row + 1
        span = (arm["id"], _point(body_nodes[0]), _point(section, True)) if body_nodes else None
        result.append((arm, span))
        pending, first = [], None
    return result


def _expression_arms(node, content: bytes, identifier: str) -> list:
    """Сохраняет pattern/when каждой switch-expression ветви отдельно от результата."""
    result = []
    for arm_node in node.named_children:
        if arm_node.type != "switch_expression_arm":
            continue
        arrow = next((child for child in arm_node.children if child.type == "=>"), None)
        if arrow is None:
            continue
        guard = next((child for child in arm_node.named_children if child.type == "when_clause"), None)
        expressions = [child for child in arm_node.named_children if child.start_byte >= arrow.end_byte]
        body = expressions[-1] if expressions else None
        label = content[arm_node.start_byte:arrow.start_byte].decode("utf-8").strip()
        kind = "default" if label == "_" else "case"
        result.append(_arm(identifier, len(result), kind, label, arm_node, body, _text(guard, content) or None))
    return result


def analyze_control_flow(declaration, content: bytes, node_id: str, events: list[dict]) -> dict:
    """Добавляет arm IDs к событиям и возвращает ограниченную AST-модель ветвления."""
    branches, spans, terminals = [], [], []
    truncated = False
    arm_count = 0
    unsupported = set()
    pending = [declaration]
    while pending:
        node = pending.pop()
        if node != declaration and node.type in _SKIPPED:
            unsupported.add("Вложенные объявления методов рассматриваются отдельно.")
            continue
        if node.type in {"try_statement", "catch_clause", "finally_clause", "goto_statement"}:
            unsupported.add(f"Переходы {node.type} не моделируются; показаны лексические ветви if, switch, ?: и циклы.")
        if node.type in {"lambda_expression", "anonymous_method_expression"}:
            unsupported.add("Порядок и вызов вложенных lambda не вычисляются.")
        if node.type in {"if_statement", "switch_statement", "switch_expression", "conditional_expression"}:
            if len(branches) >= MAX_BRANCHES:
                truncated = True
            else:
                kind = {"if_statement": "if", "switch_statement": "switch", "switch_expression": "switch-expression",
                        "conditional_expression": "conditional"}[node.type]
                digest = hashlib.sha256(f"{node_id}:{node.start_byte}:{kind}".encode()).hexdigest()[:16]
                identifier = "branch-" + digest
                branch = {"id": identifier, "kind": kind, "line": node.start_point.row + 1,
                          "endLine": node.end_point.row + 1, "condition": None, "selector": None,
                          "arms": [], "parentArmIds": []}
                arms = []
                if kind in {"if", "conditional"}:
                    branch["condition"] = _text(node.child_by_field_name("condition"), content)
                    for index, (field, arm_kind) in enumerate((("consequence", "true"), ("alternative", "false"))):
                        body = node.child_by_field_name(field)
                        if body is not None:
                            arm, span = _arm(identifier, index, arm_kind, arm_kind, body, body)
                            if kind == "conditional":
                                # В отличие от блока if, arm выбирает значение; литералы тоже нужны читателю.
                                arm["expression"] = _text(body, content)
                            arms.append((arm, span))
                elif kind == "switch":
                    branch["selector"] = _text(node.child_by_field_name("value"), content)
                    arms = _switch_arms(node, content, identifier)
                else:
                    branch["selector"] = _text(node.named_children[0], content)
                    arms = _expression_arms(node, content, identifier)
                for arm, span in arms:
                    if arm_count >= MAX_ARMS:
                        truncated = True
                        break
                    branch["arms"].append(arm)
                    arm_count += 1
                    if span:
                        spans.append(span)
                branch["_point"] = _point(node)
                branches.append(branch)
        loop_kind = {"for_statement": "for", "foreach_statement": "foreach", "while_statement": "while", "do_statement": "do"}.get(node.type)
        if loop_kind:
            if len(branches) >= MAX_BRANCHES:
                truncated = True
            else:
                digest = hashlib.sha256(f"{node_id}:{node.start_byte}:{loop_kind}".encode()).hexdigest()[:16]
                identifier = "branch-" + digest
                branch = {"id": identifier, "kind": loop_kind, "line": node.start_point.row + 1,
                          "endLine": node.end_point.row + 1, "condition": _text(node.child_by_field_name("condition"), content),
                          "selector": None, "header": _loop_header(node, content),
                          "collection": _text(node.child_by_field_name("right"), content) if loop_kind == "foreach" else "",
                          "arms": [], "parentArmIds": []}
                body = node.child_by_field_name("body")
                if body is not None and arm_count < MAX_ARMS:
                    arm, span = _arm(identifier, 0, "body", "body", body, body)
                    branch["arms"].append(arm)
                    arm_count += 1
                    if span:
                        spans.append(span)
                elif body is not None:
                    truncated = True
                branch["_point"] = _point(node)
                branches.append(branch)
        if node.type in _TERMINALS:
            if len(terminals) >= MAX_TERMINALS:
                truncated = True
            else:
                terminals.append({"kind": _TERMINALS[node.type], "text": _text(node, content),
                                  "line": node.start_point.row + 1, "endLine": node.end_point.row + 1,
                                  "branchIds": [], "_point": _point(node)})
        pending.extend(reversed(node.named_children))
    for branch in branches:
        position = branch.pop("_point")
        branch["parentArmIds"] = [identifier for identifier, start, end in spans if start <= position < end]
    for terminal in terminals:
        position = terminal.pop("_point")
        terminal["branchIds"] = [identifier for identifier, start, end in spans if start <= position < end]
    for event in events:
        position = (event["line"], event["column"])
        event["branchIds"] = [identifier for identifier, start, end in spans if start <= position < end]
    for branch in branches:
        truncated |= any(len(branch.get(field) or "") >= 1000 for field in ("condition", "selector", "header", "collection"))
        for arm in branch["arms"]:
            truncated |= any(len(arm.get(field) or "") >= 1000 for field in ("label", "condition", "expression"))
    truncated |= any(len(terminal["text"]) >= 1000 for terminal in terminals)
    return {"branches": branches, "terminals": terminals, "truncated": truncated,
            "unsupported": sorted(unsupported)}
