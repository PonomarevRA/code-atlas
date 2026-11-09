"""Ограниченные рекомендации по читаемости C#-дерева; не ошибки и не автоматические исправления."""

import hashlib

MAX_RECOMMENDATIONS = 20
MAX_AST_NODES = 20_000
MAX_RETURN_EXPRESSION_CHARS = 1000
_NESTED_SCOPES = {"method_declaration", "constructor_declaration", "local_function_statement",
                  "class_declaration", "lambda_expression", "anonymous_method_expression"}
_VALUE_BRANCHES = {"conditional_expression", "switch_expression"}
RECOMMENDATION = (
    "Рекомендуется выделить вычисление в локальную переменную result и завершить метод return result;. "
    "Подберите фактический тип возвращаемого значения и свободное имя; сохраните порядок вычислений, "
    "преобразования типов, await и область действия. Это рекомендация для проверки, не готовый autofix."
)


def empty_readability_recommendations() -> dict:
    """Возвращает независимую модель, чтобы отсутствие анализа не выглядело доказательством качества."""
    return {"sourceKind": "working-tree", "status": "unavailable", "items": [], "truncated": False,
            "limits": {"maxItems": MAX_RECOMMENDATIONS, "maxAstNodes": MAX_AST_NODES,
                       "maxReturnExpressionChars": MAX_RETURN_EXPRESSION_CHARS},
            "warning": "Рекомендации лексические. Изменять код можно только в разрешённой задаче; "
                       "семантическую эквивалентность нужно проверить по исходнику и тестам."}


def analyze_readability(declaration, content: bytes, node_id: str) -> dict:
    """Помечает длинные return и возвраты с несколькими выборами значений, исключая вложенные области."""
    result = empty_readability_recommendations()
    result["status"] = "partial" if declaration.has_error else "parsed"
    pending = [declaration]
    visited = 0
    while pending:
        node = pending.pop()
        visited += 1
        if visited > MAX_AST_NODES:
            result["truncated"] = True
            break
        if node != declaration and node.type in _NESTED_SCOPES:
            continue
        if node.type != "return_statement":
            pending.extend(reversed(node.named_children))
            continue
        expressions = [child for child in node.named_children if child.type != "comment"]
        expression = expressions[-1] if expressions else None
        if expression is None or expression.type == "ref_expression":
            continue
        text = content[expression.start_byte:expression.end_byte].decode("utf-8")
        reasons = []
        if len(text) >= MAX_RETURN_EXPRESSION_CHARS:
            reasons.append("long-return-expression")
        # Возврат из lambda принадлежит другой области; подсчёт не входит в её тело.
        value_branches = 0
        children = [expression]
        while children:
            child = children.pop()
            visited += 1
            if visited > MAX_AST_NODES:
                result["truncated"] = True
                break
            if child.type in _NESTED_SCOPES:
                continue
            if child.type in _VALUE_BRANCHES:
                value_branches += 1
                if value_branches == 2:
                    reasons.append("multiple-value-branches")
                    break
            children.extend(reversed(child.named_children))
        if not reasons:
            continue
        if len(result["items"]) >= MAX_RECOMMENDATIONS:
            result["truncated"] = True
            break
        digest = hashlib.sha256(f"{node_id}:{node.start_byte}:return-readability".encode()).hexdigest()[:16]
        result["items"].append({"id": "readability-" + digest, "kind": "extract-return-result",
                                "severity": "recommendation", "line": node.start_point.row + 1,
                                "endLine": node.end_point.row + 1, "reasons": reasons,
                                "expressionPreview": text[:160], "expressionChars": len(text),
                                "previewTruncated": len(text) > 160, "message": RECOMMENDATION})
    return result
