"""Exact Kotlin declaration selection and bounded lexical signature facts."""
from atlas.node_documentation import empty_documentation

DECLARATIONS = {'function_declaration', 'secondary_constructor'}
MAX_AST_NODES = 20_000


def text(node, content):
    """Decode a complete AST byte span; callers bound serialized values."""
    return content[node.start_byte:node.end_byte].decode('utf-8') if node is not None else ''


def body_of(node):
    """Locate only the declaration's immediate body, excluding nested declarations."""
    return next((c for c in node.named_children if c.type in {'function_body', 'block'}), None)


def name_of(node, content):
    """Secondary constructors have a keyword instead of a name field."""
    return 'constructor' if node.type == 'secondary_constructor' else text(node.child_by_field_name('name'), content)


def owner_of(node, content):
    """Retain lexical type and function owners to distinguish nested same-name methods."""
    names = []
    parent = node.parent
    while parent:
        if parent.type in DECLARATIONS | {'class_declaration', 'object_declaration'}:
            name = name_of(parent, content)
            if name:
                names.append(name)
        parent = parent.parent
    return '.'.join(reversed(names))


def select(root, node, content):
    """Resolve name, signature location and captured owner together; never choose a neighbour."""
    pending, declarations, count = [root], [], 0
    while pending:
        item = pending.pop()
        count += 1
        if count > MAX_AST_NODES:
            return None, 'Kotlin AST exceeds 20000 nodes.'
        if item.type in DECLARATIONS:
            declarations.append(item)
        pending.extend(reversed(item.children))
    name = str((node.get('memberFacts') or {}).get('name') or node.get('label') or '').lstrip('.').split('(', 1)[0]
    facts = node.get('callableFacts') or {}
    matches = [d for d in declarations if name_of(d, content) == name]
    if facts.get('kind') == 'constructor' or node.get('kind') == 'constructor':
        matches = [d for d in declarations if d.type == 'secondary_constructor']
    line = node.get('line')
    if isinstance(line, int):
        # Published symbols identify a declaration header, never arbitrary lines in its body.
        matches = [d for d in matches if d.start_point.row + 1 <= line <= (body_of(d) or d).start_point.row + 1]
    owner = facts.get('declaringType')
    if owner and owner != node.get('file'):
        matches = [d for d in matches if owner_of(d, content) == owner]
    parameter_list = facts.get('parameterList')
    if parameter_list is not None:
        matches = [d for d in matches if text(next((c for c in d.named_children if c.type == 'function_value_parameters'), None), content) == parameter_list]
    if len(matches) != 1:
        return None, 'Exact Kotlin declaration is missing or ambiguous; neighbouring bodies are excluded.'
    return matches[0], None


def declaration_facts(declaration, content):
    """Capture explicit types and adjacent method comments without inferring Kotlin types."""
    body = body_of(declaration)
    end = body.start_byte if body else declaration.end_byte
    parameters = next((c for c in declaration.named_children if c.type == 'function_value_parameters'), None)
    captured = []
    for parameter in parameters.named_children if parameters else []:
        if parameter.type != 'parameter':
            continue
        name = next((c for c in parameter.named_children if c.type == 'identifier'), None)
        colon = next((c for c in parameter.children if c.type == ':'), None)
        type_parts = [c for c in parameter.named_children if colon and c.start_byte >= colon.end_byte]
        captured.append({'name': text(name, content)[:400], 'type': text(type_parts[0], content)[:400] if type_parts else ''})
    types = [c for c in declaration.named_children if parameters and c.start_byte >= parameters.end_byte and c.end_byte <= end and c.type not in {'type_constraints', 'constructor_delegation_call'}]
    return_type = text(types[0], content)[:400] if types else ''
    documentation = empty_documentation()
    sibling, row, chars, comments = declaration.prev_named_sibling, declaration.start_point.row, 0, []
    while sibling and sibling.type in {'line_comment', 'block_comment'} and row - sibling.end_point.row <= 1:
        value = text(sibling, content)
        remaining = 8000 - chars
        if len(value) > remaining:
            documentation['truncated'] = True
        value = value[:remaining]
        comments.append({'text': value, 'line': sibling.start_point.row + 1})
        chars += len(value)
        row, sibling = sibling.start_point.row, sibling.prev_named_sibling
        if chars >= 8000:
            break
    documentation['comments'] = list(reversed(comments))
    summary = '\n'.join(c['text'] for c in documentation['comments'])
    documentation['summary'] = summary[:8000]
    documentation['truncated'] |= len(summary) > 8000
    return {'signature': content[declaration.start_byte:end].decode('utf-8').strip()[:400],
            'parameters': captured[:40], 'returnType': return_type, 'documentation': documentation,
            'declarationTruncated': len(captured) > 40 or end - declaration.start_byte > 400}
