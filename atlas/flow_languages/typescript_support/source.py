"""Bounded TypeScript declaration identity and lexical metadata, without project imports."""
import re

from atlas.node_documentation import empty_documentation

MAX_AST_NODES = 20_000
MAX_SELECTION_NODES = 20_000
CALLABLES = {'function_declaration', 'generator_function_declaration', 'method_definition',
             'method_signature', 'abstract_method_signature', 'function_signature',
             'arrow_function', 'function_expression', 'generator_function'}
TYPES = {'class_declaration', 'abstract_class_declaration', 'class', 'interface_declaration'}


def text(node, content):
    """Decode an exact Tree-sitter byte span; coordinates remain parser byte columns."""
    return content[node.start_byte:node.end_byte].decode('utf-8') if node is not None else ''


def site(node):
    """Return one-based byte columns with an exclusive end, shared by calls and events."""
    return dict(line=node.start_point.row + 1, column=node.start_point.column + 1,
                endLine=node.end_point.row + 1, endColumn=node.end_point.column + 1)


def small_declaration_candidates(root):
    """Collect declarations cheaply when the native full-tree count already proves the selection cap.

    The caller checks the full count, including punctuation. Named child lists can
    therefore never allocate an unbounded sibling set, and source parents remain intact.
    """
    nodes, pending = [], [root]
    while pending:
        current = pending.pop()
        if current.type in CALLABLES:
            nodes.append(current)
        pending.extend(reversed(current.named_children))
    return nodes


def binding_anchor(node):
    """Locate the original variable/field declaration of a bound closure, retaining its parents."""
    original = node
    parent = node.parent
    while parent is not None and parent.type in {'parenthesized_expression', 'as_expression',
                                                'satisfies_expression', 'non_null_expression'}:
        node, parent = parent, parent.parent
    if parent is not None and parent.type in {'variable_declarator', 'public_field_definition', 'pair'}:
        return parent
    return original


def bound_callable(node):
    """Follow only transparent value wrappers, never an arbitrary nested callback body."""
    if node.type not in {'variable_declarator', 'public_field_definition', 'pair'}:
        return None
    value = node.child_by_field_name('value')
    for _ in range(24):
        if value is None or value.type in CALLABLES:
            return value
        if value.type not in {'parenthesized_expression', 'as_expression', 'satisfies_expression', 'non_null_expression'}:
            return None
        value = value.named_child(0)
    return None


def declaration_candidates(root, raw):
    """Search the original tree near the requested line without walking unrelated method bodies.

    Cursor point seeks skip preceding siblings without materializing a large child list.
    Legacy identities without a line use the same bounded complete traversal; a prefix
    is never accepted as proof that one declaration is unique.
    """
    if root.descendant_count <= MAX_SELECTION_NODES:
        return small_declaration_candidates(root), False
    line = raw.get('line')
    row = line - 1 if isinstance(line, int) and not isinstance(line, bool) and line > 0 else None
    column = raw.get('column')
    point = (row, column - 1) if row is not None and isinstance(column, int) and column > 0 else None
    byte = raw.get('startByte')
    byte = byte if isinstance(byte, int) and not isinstance(byte, bool) and byte >= 0 else None
    cursor, nodes, seen, visited = root.walk(), [], set(), 0
    while True:
        current = cursor.node
        visited += 1
        if visited > MAX_SELECTION_NODES:
            return [], True
        intersects = row is None or current.start_point.row <= row <= current.end_point.row
        if byte is not None:
            intersects = current.start_byte <= byte <= current.end_byte
        elif point is not None:
            intersects = current.start_point <= point <= current.end_point
        if intersects:
            candidate = current if current.type in CALLABLES else bound_callable(current)
            if candidate is not None and candidate.id not in seen:
                nodes.append(candidate)
                seen.add(candidate.id)
            if byte is not None:
                descend = cursor.goto_first_child_for_byte(byte) is not None
            elif row is None:
                descend = cursor.goto_first_child()
            else:
                descend = cursor.goto_first_child_for_point(point or (row, 0)) is not None
            if descend:
                continue
        while True:
            if cursor.goto_next_sibling():
                if byte is not None:
                    keep_sibling = cursor.node.start_byte <= byte
                elif point is not None:
                    keep_sibling = cursor.node.start_point <= point
                else:
                    keep_sibling = row is None or cursor.node.start_point.row <= row
                if keep_sibling:
                    break
            if not cursor.goto_parent():
                return nodes, False


def callable_name(node, content):
    """Resolve named functions and variable/property-bound closures without guessing callbacks."""
    name = node.child_by_field_name('name')
    if name is not None:
        return text(name, content)
    anchor = binding_anchor(node)
    if anchor != node:
        return text(anchor.child_by_field_name('name') or anchor.child_by_field_name('key'), content)
    return ''


def owner_name(node, content):
    """Build lexical type ownership for same-name methods in different types."""
    names, parent = [], node.parent
    while parent is not None:
        if parent.type in TYPES:
            names.append(text(parent.child_by_field_name('name'), content))
        parent = parent.parent
    return '.'.join(reversed(names))


def declaration_signature(node, content):
    """Read the lexical declaration header without expanding comments or the method body."""
    body = node.child_by_field_name('body')
    end = body.start_byte if body is not None else node.end_byte
    return content[node.start_byte:end].decode('utf-8').strip()


def header_contains_line(node, line):
    """Allow legacy name lines within a decorated/multiline header, never inside its body."""
    body = node.child_by_field_name('body')
    end = body.start_point.row if body is not None else node.end_point.row
    return binding_anchor(node).start_point.row + 1 <= line <= end + 1


def select(nodes, raw, content, filename=''):
    """Use name, exact line/range, parameters and owner; stale or ambiguous facts fail closed."""
    if raw.get('kind') in {'class', 'interface', 'file', 'struct', 'enum'} or raw.get('_callable_class'):
        return None
    label = str(raw.get('label', '')).strip('.')
    name = label.split('(', 1)[0]
    candidates = [n for n in nodes if n.type in CALLABLES and callable_name(n, content) == name]
    line = raw.get('line')
    if isinstance(line, int):
        # A unique same-name neighbour must not rescue a stale source location.
        exact = [n for n in candidates if binding_anchor(n).start_point.row + 1 == line]
        candidates = exact or [n for n in candidates if header_contains_line(n, line)]
    facts = raw.get('callableFacts') or {}
    owner = facts.get('declaringType')
    if owner:
        expected = '' if owner == filename else owner
        candidates = [n for n in candidates if owner_name(n, content) == expected]
    params = facts.get('parameterList')
    if params:
        candidates = [n for n in candidates if text(n.child_by_field_name('parameters'), content) == params]
    signature = facts.get('signature')
    if signature:
        candidates = [n for n in candidates if signature in {
            callable_name(n, content) + text(n.child_by_field_name('parameters'), content),
            declaration_signature(n, content)}]
    column = raw.get('column')
    if isinstance(column, int):
        candidates = [n for n in candidates if binding_anchor(n).start_point.column + 1 == column]
    # Explicit endpoints constrain a candidate even when its name is otherwise unique.
    for field, coordinate in (
        ('startByte', lambda n: binding_anchor(n).start_byte),
        ('endByte', lambda n: binding_anchor(n).end_byte),
        ('endLine', lambda n: binding_anchor(n).end_point.row + 1),
        ('endColumn', lambda n: binding_anchor(n).end_point.column + 1),
    ):
        if isinstance(raw.get(field), int):
            candidates = [n for n in candidates if coordinate(n) == raw[field]]
    return candidates[0] if len(candidates) == 1 else None


def documentation(node, content):
    """Capture adjacent own comments, keeping class documentation separate and bounded."""
    result = empty_documentation()
    anchor = node
    while anchor.parent is not None and anchor.parent.type in {'export_statement', 'ambient_declaration', 'variable_declarator', 'lexical_declaration', 'public_field_definition'}:
        anchor = anchor.parent
    prev, row, comments, size = anchor.prev_named_sibling, anchor.start_point.row, [], 0
    while prev is not None and prev.type == 'comment' and prev.end_point.row >= row - 1:
        value = text(prev, content)
        remaining = 8000 - size
        if remaining <= 0:
            result['truncated'] = True
            break
        comments.append({'text': value[:remaining], 'line': prev.start_point.row + 1})
        result['truncated'] |= len(value) > remaining
        size += min(len(value), remaining)
        row, prev = prev.start_point.row, prev.prev_named_sibling
    comments.reverse()
    result['comments'] = comments
    lines = []
    for comment in comments:
        for line in comment['text'].splitlines():
            cleaned = re.sub(r'^\s*(?:/\*+|\*/|\*|//)\s?', '', line).removesuffix('*/').strip()
            if cleaned:
                lines.append(cleaned)
    # Tags are retained as source comments; do not pretend to parse a full JSDoc type grammar.
    result['summary'] = '\n'.join(line for line in lines if not line.startswith('@'))[:8000]
    return result


def declaration_facts(node, content):
    """Keep typed parameters, return annotation, signature and owning type as lexical facts."""
    signature = declaration_signature(node, content)
    params = node.child_by_field_name('parameters')
    parameters = []
    children = params.named_children if params is not None else []
    for parameter in children[:40]:
        if parameter.type == 'comment':
            continue
        pattern = parameter.child_by_field_name('pattern') or parameter.child_by_field_name('name')
        annotation = text(parameter.child_by_field_name('type'), content).removeprefix(':').strip()
        parameters.append({'name': text(pattern, content)[:200], 'type': annotation[:300]})
    single = node.child_by_field_name('parameter')
    if single is not None:
        parameters.append({'name': text(single, content)[:200], 'type': ''})
    parent = node.parent
    while parent is not None and parent.type not in TYPES:
        parent = parent.parent
    context = None
    if parent is not None:
        context = {'name': owner_name(node, content), 'kind': parent.type,
                   'line': parent.start_point.row + 1, 'documentation': documentation(parent, content)}
    return dict(signature=signature[:2000], parameters=parameters,
                returnType=text(node.child_by_field_name('return_type'), content).removeprefix(':').strip()[:300],
                documentation=documentation(node, content), typeContext=context,
                declarationTruncated=len(signature) > 2000 or len(children) > 40)
