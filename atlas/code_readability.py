"""Advisory expression evidence collected during the existing bounded AST walk."""

from heapq import nlargest

VERSION = "local-readability-v1"
BOOLEAN_NODES = {"binary_expression", "binary_operator", "boolean_operator"}
NEGATION_NODES = {"unary_expression", "unary_operator", "not_operator", "prefix_unary_expression"}
OPERATOR_NODES = BOOLEAN_NODES | NEGATION_NODES
TRANSPARENT = {"parenthesized_expression"}
READABILITY_NODES = OPERATOR_NODES | TRANSPARENT
LOGICAL = {"&&": "and", "and": "and", "||": "or", "or": "or"}


class ReadabilitySignals:
    """Track connected boolean syntax once; calls and deferred bodies break the group."""

    def __init__(self):
        """Keep only expression groups and their transparent ancestors, bounded by the caller."""
        self._owners = {}
        self._groups = []

    def observe(self, node, source: bytes, operator_token=None):
        """Record logical operators and negation without rescanning any AST subtree."""
        token = ""
        if operator_token is not None:
            token = operator_token
        elif node.type in OPERATOR_NODES:
            operator = node.child_by_field_name("operator")
            if operator:
                token = source[operator.start_byte:operator.end_byte].decode("utf-8")
            else:
                token = next((child.type for child in node.children if child.type in {*LOGICAL, "!", "not"}), "")
        logical = LOGICAL.get(token) if node.type in BOOLEAN_NODES else None
        negated = node.type in NEGATION_NODES and token in {"!", "not"}
        if not logical and not negated and node.type not in TRANSPARENT:
            return
        group = self._owners.get(node.parent.id) if node.parent else None
        if group is None:
            group = {"line": node.start_point.row + 1, "operators": set(), "operatorCount": 0,
                     "negations": 0, "expression": source[node.start_byte:min(node.end_byte, node.start_byte + 240)].decode("utf-8", errors="replace")}
            self._groups.append(group)
        self._owners[node.id] = group
        if logical:
            group["operators"].add(logical)
            group["operatorCount"] += 1
        group["negations"] += int(negated)

    def result(self, *, partial: bool, deferred_bodies: int):
        """Publish bounded evidence, not a calibrated human-comprehension score."""
        expressions = []
        max_operands = mixed = negations = 0
        for group in self._groups:
            if not group["operatorCount"]:
                continue
            operands = group["operatorCount"] + 1
            max_operands = max(max_operands, operands)
            mixed += int(len(group["operators"]) > 1)
            negations += group["negations"]
            if operands >= 4 or operands >= 3 and len(group["operators"]) > 1:
                expressions.append({"line": group["line"], "operandCount": operands,
                    "operators": sorted(group["operators"]), "negationCount": group["negations"],
                    "expression": group["expression"]})
        return {"version": VERSION, "status": "partial" if partial else "complete",
                "maxBooleanOperands": max_operands, "mixedBooleanExpressions": mixed,
                "booleanNegations": negations, "deferredBodyCount": deferred_bodies,
                "expressions": nlargest(8, expressions, key=lambda item: (
                    item["operandCount"] >= 5 or len(item["operators"]) > 1,
                    item["operandCount"], item["negationCount"])),
                "evidenceTruncated": len(expressions) > 8,
                "scope": "local connected boolean syntax; calls and deferred bodies excluded",
                "interpretation": "advisory syntax signals; semantic readability not measured"}


def readability_findings(metrics: dict, *, filename: str, line=None) -> list[dict]:
    """Flag dense expressions and surface deferred inventory without penalizing simple guards."""
    from .code_health_policy import finding

    signals = metrics.get("readability") or {}
    findings = []
    for expression in signals.get("expressions", []):
        operands = expression["operandCount"]
        if operands >= 5 or operands >= 3 and len(expression["operators"]) > 1:
            findings.append(finding("dense-boolean-expression", file=filename,
                                    line=expression["line"], evidence=expression))
    if signals.get("deferredBodyCount", 0):
        findings.append(finding("deferred-logic-inventory", file=filename, line=line, severity="info",
                                evidence={"excludedDeferredBodies": signals["deferredBodyCount"]}))
    return findings
