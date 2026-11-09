"""Bounded call-site evidence keeps syntax candidates separate from resolved dependencies."""

from collections import defaultdict
import re

from .documentation_health import text
from .code_complexity import DEFERRED

VERSION = 'health-call-facts-v2'
CALL_NODES = {'call', 'call_expression', 'invocation_expression', 'method_invocation', 'macro_invocation'}
WRITE_NODES = {'assignment_expression', 'assignment', 'augmented_assignment', 'update_expression',
               'postfix_unary_expression', 'inc_statement', 'dec_statement', 'compound_assignment_expr'}
MEMBER_NODES = {'member_access_expression', 'member_expression', 'attribute', 'subscript_expression',
                'element_access_expression', 'index_expression', 'field_expression'}
UNCERTAIN = frozenset({'inferred', 'ambiguous', 'unknown', 'unresolved', 'unsupported', 'candidate', 'low'})
NO_TARGETS = frozenset()


def call_name(expression: str) -> str:
    """Extract a terminal identifier without claiming receiver or overload resolution."""
    value = expression.rstrip()
    if value.endswith('>'):
        # Only the trailing generic argument list belongs to the terminal name;
        # nested arguments must not leave an unmatched outer bracket behind.
        depth = 0
        for index in range(len(value) - 1, -1, -1):
            if value[index] == '>':
                depth += 1
            elif value[index] == '<':
                depth -= 1
                if depth == 0:
                    value = value[:index].rstrip(': ')
                    break
    match = re.search(r'([A-Za-z_$][\w$]*)$', value)
    return match[1] if match else ''


def _span(node) -> dict:
    """Store absolute UTF-8 byte offsets and one-based lines/columns for one syntax node."""
    return {'startByte': node.start_byte, 'endByte': node.end_byte,
            'startLine': node.start_point.row + 1, 'endLine': node.end_point.row + 1,
            'startColumn': node.start_point.column + 1, 'endColumn': node.end_point.column + 1}


def _call_fact(child, content: bytes, language: str) -> dict:
    """Describe lexical syntax; contextual keywords and BCL names need binding evidence."""
    function = child.child_by_field_name('function') or child.child_by_field_name('name') or child.child_by_field_name('macro')
    expression = text(function, content)
    receiver = None
    if function is not None and function.type in MEMBER_NODES:
        receiver = text(function.child_by_field_name('expression') or function.child_by_field_name('object')
                        or function.child_by_field_name('value'), content) or None
    if child.type == 'method_invocation':
        receiver = text(child.child_by_field_name('object'), content) or None
        if receiver:
            expression = receiver + '.' + expression
    if child.type == 'macro_invocation':
        expression += '!'  # Macro expansion is not an ordinary resolved call.
    reason = 'binding-unavailable'
    category, resolution = 'unresolved', 'unknown'
    if language == 'csharp' and function is not None and function.type == 'identifier' and expression == 'nameof':
        # Tree-sitter also uses this shape when @nameof or a static import shadows
        # the contextual keyword. A single file cannot prove the project binding.
        category, resolution, reason = 'language-construct-candidate', 'ambiguous', 'contextual-keyword-binding-unavailable'
    return {'language': language, 'expression': expression[:300], 'name': call_name(expression),
            'receiver': receiver[:300] if receiver else None, 'line': child.start_point.row + 1,
            'span': _span(child), 'category': category, 'resolution': resolution, 'reason': reason,
            'evidence': {'kind': 'lexical-syntax', 'syntaxKind': child.type}}


def capture_call_facts(declaration, content: bytes, language: str = '') -> dict:
    """Collect calls and writes in one bounded local-body pass, excluding deferred bodies.

    A grammar-specific nameof_expression is a construct, while invocation-shaped
    nameof remains ambiguous. No API is confirmed from its textual name.
    """
    body = declaration.child_by_field_name('body') or next((n for n in declaration.named_children if n.type == 'arrow_expression_clause'), None)
    pending = [body] if body is not None else []
    calls, constructs, writes, visited, writes_unknown = [], [], set(), 0, False
    while pending and visited < 50000:
        child = pending.pop()
        visited += 1
        if child is not body and child.type in DEFERRED:
            continue
        if language == 'csharp' and child.type == 'nameof_expression':
            constructs.append({'category': 'language-construct', 'resolution': 'confirmed',
                               'name': 'nameof', 'span': _span(child), 'evidence': {'kind': 'grammar-nameof-expression'}})
            continue  # Its operand is inspected by the compiler, not executed.
        if child.type in CALL_NODES:
            if len(calls) >= 200:
                pending.append(child)
                break
            calls.append(_call_fact(child, content, language))
        if child.type in WRITE_NODES:
            left = child.child_by_field_name('left') or child.child_by_field_name('argument') or (child.named_children[0] if child.named_children else None)
            if left is not None:
                if left.type in MEMBER_NODES:
                    writes.add(text(left, content)[:300])
                elif language != 'python':
                    writes_unknown = True  # An unqualified assignment may write a field.
        pending.extend(reversed(child.named_children))
    return {'version': VERSION, 'language': language,
            'status': 'partial' if pending or declaration.has_error else 'complete',
            'calls': calls, 'constructs': constructs, 'truncated': bool(pending),
            'lexicalWrites': len(writes), 'writesUnknown': writes_unknown,
            'scope': 'local body; deferred bodies and callees excluded'}


def confirmed_call_edge(edge: dict, source_node: dict | None = None) -> bool:
    """Reject explicit uncertainty or stale source evidence before using a static edge."""
    evidence = edge.get('evidence') or {}
    captured_hash = edge.get('sourceHash') or evidence.get('sourceHash')
    if captured_hash:
        source_hash = ((source_node or {}).get('complexity') or {}).get('sourceHash')
        if source_hash:
            for candidate in (edge.get('sourceHash'), evidence.get('sourceHash')):
                if candidate and candidate != source_hash:
                    return False
    for key in ('confidence', 'graphConfidence', 'resolution', 'resolutionStatus'):
        value = edge.get(key)
        if value and str(value).lower() in UNCERTAIN:
            return False
    if evidence:
        for key in ('confidence', 'graphConfidence', 'resolution', 'resolutionStatus'):
            value = evidence.get(key)
            if value and str(value).lower() in UNCERTAIN:
                return False
    return True


def _edge_site(edge: dict) -> tuple[tuple | None, int | None]:
    """Read exact call spans or a legacy line, never digits embedded in a filename."""
    evidence = edge.get('evidence') or {}
    site = edge.get('callSite') or evidence.get('callSite') or edge.get('span') or evidence.get('span') or evidence
    exact = None
    if isinstance(site, dict) and isinstance(site.get('startByte'), int) and isinstance(site.get('endByte'), int):
        exact = site['startByte'], site['endByte']
    line = edge.get('line') or evidence.get('line')
    if not isinstance(line, int):
        match = re.search(r'(?:^L?|:)(\d+)$', str(edge.get('source_location', '')))
        line = int(match[1]) if match else None
    return exact, line


def resolved_call_facts(node: dict, outgoing: list[dict], nodes: dict) -> list[dict] | None:
    """Index static edges once and resolve only unique call-site evidence.

    Repeated calls on one line cannot share a legacy line-only fact. Owner-local
    fallback additionally requires one unqualified call and one target binding.
    """
    facts = node.get('healthCallFacts')
    if not facts or facts.get('status') != 'complete':
        return None
    calls = facts.get('calls', [])
    if not calls:
        return []
    if not outgoing:
        # With no graph evidence every lexical call keeps its original unknown
        # status; no matching indexes or duplicate call dictionaries are needed.
        return list(calls)
    by_span, by_line, by_local = defaultdict(set), defaultdict(set), defaultdict(set)
    owner = (node.get('memberFacts') or {}).get('ownerNodeId')
    for edge in outgoing:
        if not confirmed_call_edge(edge, node):
            continue
        target_id = edge.get('target')
        target = nodes.get(target_id)
        if not target:
            continue
        target_name = (target.get('memberFacts') or {}).get('name') or str(target.get('label', '')).split('(')[0]
        name = call_name(target_name)
        if not name:
            continue
        span, line = _edge_site(edge)
        if span is not None:
            by_span[(name, span)].add(target_id)
        elif line is not None:
            by_line[(name, line)].add(target_id)
        elif owner and owner == (target.get('memberFacts') or {}).get('ownerNodeId'):
            by_local[name].add(target_id)
    if not by_span and not by_line and not by_local:
        return list(calls)
    line_counts, name_counts = defaultdict(int), defaultdict(int)
    for call in calls:
        name = call.get('name')
        line_counts[(name, call.get('line'))] += 1
        name_counts[name] += 1
    resolved = []
    for call in calls:
        name, line = call.get('name'), call.get('line')
        span = call.get('span') or {}
        exact = span.get('startByte'), span.get('endByte')
        matches = by_span.get((name, exact), NO_TARGETS)
        basis = 'exact-call-site'
        if not matches and line_counts[(name, line)] == 1:
            matches = by_line.get((name, line), NO_TARGETS)
            basis = 'unique-legacy-call-line'
        if not matches and call.get('expression', '').isidentifier() and name_counts[name] == 1:
            matches = by_local.get(name, NO_TARGETS)
            basis = 'unique-owner-local-binding'
        if len(matches) == 1:
            result = dict(call)
            target_id = next(iter(matches))
            result.update(category='external-call' if nodes[target_id].get('kind') == 'external' else 'project-call',
                          resolution='confirmed', target=target_id, reason=basis,
                          evidence={'kind': 'static-graph', 'basis': basis, 'runtimeSelectionKnown': False})
        elif matches:
            result = dict(call)
            result.update(resolution='ambiguous', reason='multiple-static-targets')
        else:
            result = call  # Unchanged captured evidence does not need another dictionary.
        resolved.append(result)
    return resolved


def unmatched_calls(node: dict, outgoing: list[dict], nodes: dict) -> list[dict] | None:
    """Return unresolved call sites, retaining None when lexical coverage is incomplete."""
    resolved = resolved_call_facts(node, outgoing, nodes)
    if resolved is None:
        return None
    return [call for call in resolved if call.get('resolution') != 'confirmed']
