"""Извлечение документационного покрытия из принадлежащих объявлению комментариев."""

import ast as python_ast
import inspect
from decimal import Decimal
import re
import xml.etree.ElementTree as ET

from .code_health_policy import finding, policy, round_score
from . import rust_support
from .documentation_ownership import comment_anchor, occupied_lines
from .documentation_parameters import parameter_described, parameter_inventory, parameter_names

VERSION = "documentation-coverage-v2"
SUPPORTED = {"csharp", "python", "java", "javascript", "typescript", "go", "rust"}
MAX_TEXT = 8000
MAX_NODES = 50000
CONTROLS = {"if_statement", "if_expression", "elif_clause", "conditional_expression", "switch_statement",
            "switch_expression", "for_statement", "for_in_statement", "for_each_statement", "foreach_statement",
            "while_statement", "do_statement"} | rust_support.CONTROLS
DEFERRED = {"lambda_expression", "lambda", "arrow_function", "function_expression", "func_literal",
            "function_definition", "function_declaration", "method_declaration", "method_definition", "local_function_statement"} | rust_support.DEFERRED


def text(node, source: bytes) -> str:
    """Читает только заданный AST диапазон без выполнения исходника."""
    return source[node.start_byte:node.end_byte].decode("utf-8") if node is not None else ""


def _adjacent(declaration, source: bytes, language: str, *, ranges=None) -> str:
    """Python docstring и непосредственно прилегающий comment принадлежат самому методу."""
    if language == "rust":
        return rust_support.declaration_comment(declaration, source, ranges=ranges)
    if language == "python":
        body = declaration.child_by_field_name("body")
        if body and body.named_children:
            first = body.named_children[0]
            if first.type == "expression_statement" and first.named_children and first.named_children[0].type == "string":
                try:
                    value = python_ast.literal_eval(text(first.named_children[0], source))
                    if isinstance(value, str):
                        if ranges is not None:
                            ranges.append(occupied_lines(first))
                        return inspect.cleandoc(value)
                except (ValueError, SyntaxError):
                    return ""
    if language in {"javascript", "typescript", "python"}:
        declaration = comment_anchor(declaration)
    pieces = []
    sibling, line = declaration.prev_named_sibling, declaration.start_point.row
    while sibling and "comment" in sibling.type and line - sibling.end_point.row <= 1 and len(pieces) < 80:
        pieces.append(text(sibling, source))
        if ranges is not None:
            ranges.append(occupied_lines(sibling))
        line, sibling = sibling.start_point.row, sibling.prev_named_sibling
    return "\n".join(reversed(pieces))


def owned_documentation_lines(declaration, source: bytes, language: str) -> list[tuple[int, int]]:
    """Return exact owned docstring/comment lines using the same rules as D scoring."""
    ranges = []
    _adjacent(declaration, source, language, ranges=ranges)
    return sorted(ranges)


def _clean_comment(value: str) -> str:
    """Снимает маркеры комментария, оставляя исходный язык и формулировки."""
    value = re.sub(r"^/\*+|\*/$", "", value.strip())
    return "\n".join(re.sub(r"^\s*(?:///|//|\*)\s?", "", line) for line in value.splitlines()).strip()


def _parse_documentation(value: str, language: str) -> dict:
    """Нормализует XML, Javadoc/JSDoc, docstrings и GoDoc; смысл текста не оценивает."""
    result = {"purpose": "", "params": {}, "returns": "", "exceptions": [], "inheritdoc": None,
              "status": "complete", "truncated": len(value) > MAX_TEXT}
    clean = value[:MAX_TEXT] if language == 'rust' else _clean_comment(value[:MAX_TEXT])
    if language == "rust":
        clean = rust_support.documentation_sections(clean)
    if language == "csharp" and re.search(r"</?[A-Za-z]", clean):
        if "<!DOCTYPE" in clean.upper() or "<!ENTITY" in clean.upper():
            result["status"] = "unavailable"
            return result
        try:
            root = ET.fromstring("<doc>" + clean + "</doc>")
        except ET.ParseError:
            result["status"] = "unavailable"
            return result
        def xml_text(node):
            return " ".join(" ".join(node.itertext()).split()) if node is not None else ""
        result["purpose"] = xml_text(root.find("summary"))
        result["returns"] = xml_text(root.find("returns"))
        result["params"] = {p.get("name", "").removeprefix("@"): xml_text(p) for p in root.findall("param")[:40]}
        result["exceptions"] = [xml_text(p) for p in root.findall("exception")[:40]]
        inherited = root.find("inheritdoc")
        if inherited is not None:
            result["inheritdoc"] = inherited.get("cref", "")
        result["truncated"] |= len(root.findall("param")) > 40 or len(root.findall("exception")) > 40
    else:
        summary = []
        section = "purpose"
        for line in clean.splitlines():
            stripped = line.strip()
            if "{@inheritDoc}" in stripped:
                result["inheritdoc"] = ""
                continue
            parameter = re.match(r"(?:@param\s+(?:\{[^}]*\}\s*)?|:param\s+(?:\w+\s+)?)([\w$.\[\]=*]+)\s*:?[ -]*(.*)", stripped)
            if parameter:
                name = parameter[1].strip("[]").split("=")[0].lstrip("*").removeprefix("...")
                result["params"][name] = parameter[2]
                section = "tags"
                continue
            returned = re.match(r"(?:@returns?\s*|:returns?:\s*)(.*)", stripped)
            if returned:
                result["returns"] = returned[1]
                section = "tags"
                continue
            raised = re.match(r"(?:@throws?\s+|:raises?\s+)(.*)", stripped)
            if raised:
                result["exceptions"].append(raised[1])
                section = "tags"
                continue
            if re.match(r"(?:Args|Arguments|Parameters)\s*:?$", stripped, re.I):
                section = "params"
                continue
            if re.match(r"(?:Returns|Yields)\s*:?$", stripped, re.I):
                section = "returns"
                continue
            if re.match(r"(?:Raises|Exceptions)\s*:?$", stripped, re.I):
                section = "exceptions"
                continue
            if section == "params":
                match = re.match(r"([*\w$]+)\s*(?:\([^)]*\))?\s*:\s*(.+)", stripped)
                if match:
                    result["params"][match[1].lstrip("*")] = match[2]
            elif section == "returns" and stripped and not re.fullmatch(r"[-=]+", stripped):
                result["returns"] += " " + stripped
            elif section == "exceptions" and stripped:
                result["exceptions"].append(stripped)
            elif section == "purpose" and not stripped.startswith(("@", ":")):
                summary.append(line)
        result["purpose"] = " ".join(summary).strip()
    if language in {"go", "rust"} and re.search(r"\breturns?\b|возвращ", result["purpose"], re.I):
        result["returns"] = result["purpose"]
    if result["truncated"]:
        result["status"] = "partial"
    return result



def _body_facts(declaration, source: bytes, metrics: dict) -> dict:
    """Один bounded обход связывает комментарии со сложными областями, исключая deferred тела."""
    body = declaration.child_by_field_name("body")
    if body is None:
        body = next((n for n in declaration.named_children if n.type == "arrow_expression_clause"), None)
    result = {"hasBody": body is not None, "hasResult": False, "throws": False,
              "regions": [], "truncated": False, "trivial": False}
    returned = declaration.child_by_field_name("returns") or declaration.child_by_field_name("return_type") or declaration.child_by_field_name("result")
    if returned and text(returned, source) not in {"void", "None", "NoneType", "()", "!"}:
        result["hasResult"] = True
    if declaration.type == "arrow_function" and body is not None and body.type not in {"block", "statement_block"}:
        result["hasResult"] = True
    if body is None:
        return result
    pending, visited = [(body, 0)], 0
    while pending and visited < MAX_NODES:
        node, depth = pending.pop()
        visited += 1
        if node is not body and node.type in DEFERRED:
            continue
        if node.type in {"return_statement", "yield_statement"} and node.named_children:
            result["hasResult"] = True
        result["throws"] |= node.type in {"throw_statement", "raise_statement"}
        nested = depth + int(node.type in CONTROLS)
        if node.type in CONTROLS and (nested >= 3 or metrics.get("branchCount", 0) >= 5):
            previous = node.prev_named_sibling
            explained = bool(previous and "comment" in previous.type and node.start_point.row - previous.end_point.row <= 2)
            for child in node.named_children:
                if "comment" in child.type and child.start_point.row <= node.start_point.row + 3:
                    explained = True
                if child.type in {"block", "statement_block"} and child.named_children:
                    explained |= "comment" in child.named_children[0].type
            if len(result["regions"]) < 100:
                result["regions"].append({"line": node.start_point.row + 1, "kind": node.type, "explained": explained})
            else:
                result["truncated"] = True
        pending.extend((child, nested) for child in reversed(node.named_children))
    result["truncated"] |= bool(pending)
    statements = [n for n in body.named_children if "comment" not in n.type]
    if not statements and body.type in {"block", "statement_block"}:
        result["trivial"] = True
    elif len(statements) == 1 and statements[0].type == "return_statement":
        expression = statements[0].named_children
        result["trivial"] = len(expression) == 1 and expression[0].type in {"identifier", "member_access_expression", "attribute", "member_expression"}
    return result


def capture_documentation_health(declaration, source: bytes, language: str, metrics: dict,
                                 *, filename: str, source_hash: str, source_kind: str) -> dict:
    """Публикует собственный документационный контракт и честный статус покрытия."""
    name = text(declaration.child_by_field_name("name"), source)
    if not name and declaration.type in {"arrow_function", "function_expression"} and declaration.parent and declaration.parent.type == "variable_declarator":
        name = text(declaration.parent.child_by_field_name("name"), source)
    raw = _adjacent(declaration, source, language)
    doc = _parse_documentation(raw, language)
    body = _body_facts(declaration, source, metrics)
    names, parameters_complete = parameter_inventory(declaration, source, language)
    if len(names) > 40:
        doc.update(status="partial", truncated=True)
        names = names[:40]
    purpose = doc["purpose"]
    placeholder = bool(purpose and (re.fullmatch(r"(?:TODO|FIXME|TBD|описание|summary)[.!\s]*", purpose, re.I)
                        or re.sub(r"[\W_]", "", purpose).casefold() == re.sub(r"[\W_]", "", name).casefold()))
    named_function = declaration.type in {"arrow_function", "function_expression"} and declaration.parent and declaration.parent.type == "variable_declarator"
    eligible = not body["trivial"] and (named_function or declaration.type not in {"lambda_expression", "lambda", "arrow_function", "function_expression", "func_literal", "closure_expression", "async_block", "const_block"})
    purpose_ok = bool(purpose) and not placeholder
    requirements = [{"group": "purpose", "name": "independent-purpose", "covered": purpose_ok}]
    for parameter in names:
        described = parameter_described(parameter, doc, language)
        requirements.append({"group": "parameters", "name": "parameter:" + parameter, "covered": described})
    if body["hasResult"]:
        requirements.append({"group": "contract", "name": "result", "covered": bool(doc["returns"])})
    if body["throws"]:
        requirements.append({"group": "contract", "name": "explicit-exceptions", "covered": bool(doc["exceptions"])})
    for region in body["regions"]:
        requirements.append({"group": "explanations", "name": "decision", "line": region["line"], "covered": region["explained"]})
    status = doc["status"]
    if language not in SUPPORTED or declaration.has_error:
        status = "unavailable"
    elif body["truncated"] or doc["inheritdoc"] is not None:
        status = "partial"
    if language == 'rust':
        parameters = declaration.child_by_field_name('parameters')
        for parameter in parameters.named_children if parameters else []:
            pattern = parameter.child_by_field_name('pattern')
            if pattern is not None and pattern.type != 'identifier':
                status = 'partial'  # Сложный pattern не доказывает полный список входного контракта.
    if not parameters_complete and status != 'unavailable':
        status = 'partial'  # Неразобранный binding не становится методом без параметров.
    exempt = not eligible and status == "complete"
    result = {"version": VERSION, "status": "not-applicable" if exempt else status, "eligible": eligible,
              "score": None, "ownDescription": purpose_ok, "documented": purpose_ok if status == "complete" else None,
              "origin": "declaration", "purpose": purpose[:500], "requirements": requirements,
              "groups": {}, "semanticReview": {"status": "not-reviewed"},
              "sourceKind": source_kind, "sourceHash": source_hash, "file": filename,
              "line": declaration.start_point.row + 1, "truncated": doc["truncated"] or body["truncated"],
              "inheritdoc": doc["inheritdoc"], "parameterNames": names, "contract": doc,
              "findings": [], "exemptReason": "trivial-accessor-or-empty-body" if not eligible else None}
    if eligible and status == "complete":
        if not purpose_ok:
            result["findings"].append(finding("placeholder-documentation" if placeholder else "missing-purpose", file=filename, line=result["line"]))
        if any(r["group"] == "contract" and not r["covered"] for r in requirements):
            result["findings"].append(finding("missing-contract", file=filename, line=result["line"]))
        if any(r["group"] == "explanations" and not r["covered"] for r in requirements):
            result["findings"].append(finding("missing-explanations", file=filename, line=result["line"]))
    if doc["inheritdoc"] is not None:
        result["findings"].append(finding("unresolved-inheritdoc", file=filename, line=result["line"], severity="info"))
    score_documentation(result)
    return result


def score_documentation(result: dict) -> None:
    """Ограничивает параметры 10 D-баллами без перенормирования их доли.

    Остальные применимые группы нормируются отдельно. Неизвестные/неприменимые
    факты не превращаются в бонус, число параметров и длина комментария не влияют
    на максимальную долю. Смысл описаний требует отдельного ревью.

    :param result: Изменяемые факты документации: requirements/status/eligible и provenance.
        Метод обновляет score, groups, parameterCoverage и связанные findings.
    """
    rules = policy()
    numerator = denominator = Decimal(0)
    for group, weight in rules['documentationWeights'].items():
        items = [r for r in result['requirements'] if r['group'] == group]
        covered = sum(bool(r['covered']) for r in items)
        result['groups'][group] = {'required': len(items), 'covered': covered}
        if items:
            numerator += Decimal(str(weight)) * covered / len(items)
            denominator += Decimal(str(weight))
    parameters = [r for r in result['requirements'] if r['group'] == 'parameters']
    covered = sum(bool(r['covered']) for r in parameters)
    result['groups']['parameters'] = {'required': len(parameters), 'covered': covered}
    known = result['status'] == 'complete'
    if not known:
        coverage_status = result['status']
    elif parameters:
        coverage_status = 'complete'
    else:
        coverage_status = 'not-applicable'
    result['parameterCoverage'] = {
        'status': coverage_status,
        'required': len(parameters), 'covered': covered,
        'coveragePercent': round(100 * covered / len(parameters), 1) if known and parameters else None,
        'missing': [r['name'].removeprefix('parameter:') for r in parameters if not r['covered']],
        'maximumDocumentationPoints': 100 * rules['parameterDocumentationShare'] if parameters else 0,
    }
    score = 100 * numerator / denominator if denominator else None
    if parameters and score is not None:
        share = Decimal(str(rules['parameterDocumentationShare']))
        score = (1 - share) * score + 100 * share * covered / len(parameters)
    result['score'] = round_score(score) if score is not None and known else None
    # Inheritdoc resolution invokes the same scorer, so its advice must reflect
    # the resolved parameter contract rather than the original partial state.
    result['findings'] = [f for f in result.get('findings', []) if f['rule'] != 'missing-parameter-description']
    if known and result.get('eligible') and result['parameterCoverage']['missing']:
        result['findings'].append(finding('missing-parameter-description', file=result.get('file', ''), line=result.get('line'),
                                         severity='info', evidence={'missingParameters': result['parameterCoverage']['missing']}))
