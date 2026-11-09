"""Bounded C++ declaration identity and adjacent source documentation."""
from ...node_documentation import empty_documentation

MAX_AST = 20_000


def text(node, source):
    """Decode only a parser-owned byte range."""
    return source[node.start_byte:node.end_byte].decode('utf-8') if node else ''


def walk(root):
    """Visit named syntax nodes iteratively; callers enforce their own count limit."""
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.children))


def function_declarator(declaration):
    """Find the declaring function, including pointer-return wrappers."""
    node = declaration.child_by_field_name('declarator')
    while node is not None and node.type != 'function_declarator':
        node = node.child_by_field_name('declarator')
    return node


def identity(declaration, source):
    """Return qualified lexical name, excluding template arguments and parameters."""
    declarator = function_declarator(declaration)
    name = text(declarator.child_by_field_name('declarator'), source) if declarator else ''
    owners = []
    current = declaration.parent
    while current is not None:
        if current.type in {'namespace_definition', 'class_specifier', 'struct_specifier'}:
            owner = text(current.child_by_field_name('name'), source)
            if owner:
                owners.append(owner)
        current = current.parent
    return '::'.join(list(reversed(owners)) + [name])


def select(nodes, node, source):
    """Resolve name, range and captured parameter syntax together; never pick a neighbour."""
    facts = node.get('callableFacts') or {}
    requested = (node.get('memberFacts') or {}).get('name') or str(node.get('label') or '').strip('.').split('(', 1)[0]
    requested = requested.replace('.', '::')
    matches = []
    for candidate in nodes:
        if candidate.type != 'function_definition':
            continue
        name = identity(candidate, source)
        if not (name == requested or name.endswith('::' + requested)):
            continue
        line = node.get('line')
        if line is not None and not candidate.start_point.row + 1 <= int(line) <= candidate.end_point.row + 1:
            continue
        declarator = function_declarator(candidate)
        params = text(declarator.child_by_field_name('parameters'), source) if declarator else ''
        captured_params = facts.get('parameterList')
        if captured_params and captured_params != params:
            continue
        owner = facts.get('declaringType')
        # Captured module paths are not a lexical namespace/type qualifier.
        if owner and not any(c in owner for c in '/\\') and not owner.endswith(('.cpp', '.hpp', '.cc', '.cxx')):
            qualifier = name.rsplit('::', 1)[0] if '::' in name else ''
            owner = owner.replace('.', '::')
            if not (qualifier == owner or qualifier.endswith('::' + owner)):
                continue
        matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def documentation(declaration, source):
    """Read only immediately adjacent comments; template wrappers own their comments."""
    anchor = declaration
    if declaration.parent and declaration.parent.type == 'template_declaration':
        anchor = declaration.parent
    result = empty_documentation()
    previous = anchor.prev_named_sibling
    row = anchor.start_point.row
    comments = []
    remaining = 8000
    while previous is not None and previous.type == 'comment' and previous.end_point.row >= row - 1:
        value = text(previous, source)
        comments.append({'text': value[:remaining], 'line': previous.start_point.row + 1})
        remaining -= len(value)
        if remaining <= 0:
            result['truncated'] = True
            break
        row, previous = previous.start_point.row, previous.prev_named_sibling
    comments.reverse()
    result['comments'] = comments
    result['summary'] = '\n'.join(c['text'].strip('/ *\n') for c in comments)[:8000]
    return result


def declaration_facts(declaration, source):
    """Publish literal parameter/signature facts without resolving C++ types."""
    body = declaration.child_by_field_name('body')
    declarator = function_declarator(declaration)
    parameters = declarator.child_by_field_name('parameters') if declarator else None
    result = []
    for parameter in parameters.named_children[:40] if parameters else []:
        if parameter.type == 'comment':
            continue
        result.append({'name': text(parameter.child_by_field_name('declarator'), source)[:400],
                       'type': text(parameter.child_by_field_name('type'), source)[:400]})
    signature = source[declaration.start_byte:body.start_byte].decode('utf-8').strip() if body else text(declaration, source)
    return {'signature': signature[:400], 'parameters': result,
            'returnType': text(declaration.child_by_field_name('type'), source)[:400],
            'documentation': documentation(declaration, source),
            'declarationTruncated': len(signature) > 400 or bool(parameters and len(parameters.named_children) > 40)}
