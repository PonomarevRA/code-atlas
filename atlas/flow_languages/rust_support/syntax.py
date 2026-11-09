"""Bounded Rust declaration selection and source facts; never load analyzed code."""
import re

from ...node_documentation import empty_documentation
from ...rust_support import declaration_comment, scope_name

MAX_AST_NODES = 20_000
MAX_TEXT = 400
DECLARATIONS = {'function_item', 'function_signature_item'}
OWNERS = {'impl_item', 'trait_item', 'mod_item', 'function_item'}


def text(node, content):
    """Decode one complete UTF-8 syntax span; callers bound published strings."""
    return content[node.start_byte:node.end_byte].decode('utf-8') if node is not None else ''


def site(node):
    """Use one-based UTF-8 byte columns and exclusive ends, matching the host."""
    return {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
            'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1}


def scan(root):
    """Collect declarations iteratively, rejecting over-budget files before extraction."""
    pending, declarations, count = [root], [], 0
    while pending:
        current = pending.pop()
        count += 1
        if count > MAX_AST_NODES:
            return [], True
        if current.type in DECLARATIONS:
            declarations.append(current)
        pending.extend(reversed(current.named_children))
    return declarations, False


def owner_name(node, content):
    """Retain impl/trait/module/local-function ancestry rather than guessing dispatch."""
    parts, parent = [], node.parent
    while parent is not None:
        if parent.type in OWNERS:
            parts.append(scope_name(parent, content))
        parent = parent.parent
    return '.'.join(reversed(parts))


def select(declarations, node, content):
    """Resolve a named exact declaration; stale positions never select a neighbor."""
    member = node.get('memberFacts') or {}
    facts = node.get('callableFacts') or {}
    name = member.get('name') or str(node.get('label') or '').strip('.').split('(', 1)[0]
    name = name.removeprefix('r#')
    matches = []
    for declaration in declarations:
        actual = text(declaration.child_by_field_name('name'), content).removeprefix('r#')
        if actual == name:
            matches.append(declaration)
    line = node.get('line')
    if isinstance(line, int):
        matches = [d for d in matches if d.start_point.row + 1 <= line <= d.end_point.row + 1]
        # A local function can be enclosed by a same-name outer function: exact start wins.
        exact = [d for d in matches if d.start_point.row + 1 == line]
        matches = exact or matches
    parameters = facts.get('parameterList')
    if parameters:
        normalize = lambda value: re.sub(r'\s+', '', value)
        matches = [d for d in matches if normalize(text(d.child_by_field_name('parameters'), content)) == normalize(parameters)]
    owner = facts.get('declaringType')
    if len(matches) > 1 and owner:
        matches = [d for d in matches if owner_name(d, content) == re.sub(r'\s+', '', owner)]
    column = node.get('column')
    if isinstance(column, int):
        matches = [d for d in matches if d.start_point.column + 1 == column]
    if len(matches) != 1:
        return None, 'Rust declaration is unresolved or ambiguous; name/range/owner must identify one function.'
    return matches[0], None


def facts(declaration, content):
    """Publish the selected signature, lexical parameters and its own rustdoc only."""
    body = declaration.child_by_field_name('body')
    end = body.start_byte if body is not None else declaration.end_byte
    signature = content[declaration.start_byte:end].decode('utf-8').strip()
    parameters = []
    parameter_list = declaration.child_by_field_name('parameters')
    if parameter_list is not None:
        for parameter in parameter_list.named_children[:40]:
            if parameter.type == 'self_parameter':
                parameters.append({'name': 'self', 'type': text(parameter, content)[:MAX_TEXT]})
            elif parameter.type == 'parameter':
                parameters.append({'name': text(parameter.child_by_field_name('pattern'), content)[:MAX_TEXT],
                                   'type': text(parameter.child_by_field_name('type'), content)[:MAX_TEXT]})
    comment = declaration_comment(declaration, content)
    documentation = empty_documentation()
    documentation.update(summary=comment[:8000], truncated=len(comment) > 8000)
    return {'signature': signature[:MAX_TEXT], 'parameters': parameters,
            'returnType': text(declaration.child_by_field_name('return_type'), content)[:MAX_TEXT] or '()',
            'documentation': documentation, 'typeContext': None,
            'declarationTruncated': len(signature) > MAX_TEXT or bool(parameter_list and parameter_list.named_child_count > 40),
            'line': declaration.start_point.row + 1, 'endLine': declaration.end_point.row + 1}
