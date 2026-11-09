"""Bounded Swift declaration selection; only host-supplied source is inspected."""
import re

from .parsed_source import parsed_tree, source_bytes
from ..node_documentation import empty_documentation
from .swift_support.flow import SwiftFlow, MAX_AST_NODES

VERSION = 'swift-method-flow-v1'
_DECLARATIONS = {'function_declaration', 'protocol_function_declaration', 'init_declaration'}


def _text(node, content):
    """Read one AST span without changing the source or loading project code."""
    return content[node.start_byte:node.end_byte].decode('utf-8') if node else ''


def _name(node, content):
    """Swift init has an unnamed token; functions have a declaration name field."""
    if node.type == 'init_declaration':
        return 'init'
    return _text(node.child_by_field_name('name'), content).strip('`')


def _select(root, content, node):
    """Require name and exact declaration start; never borrow a neighbouring method."""
    name = (node.get('memberFacts') or {}).get('name')
    if not name:
        name = str(node.get('label', '')).lstrip('.').split('(', 1)[0].split('.')[-1].strip('`')
    if name in {'init?', 'init!'}:
        name = 'init'
    pending, matches, count = [root], [], 0
    while pending:
        item = pending.pop()
        count += 1
        if count > MAX_AST_NODES:
            return None, 'Swift AST exceeds 20000 nodes; declaration selection is incomplete.'
        if item.type in _DECLARATIONS and _name(item, content) == name:
            matches.append(item)
        pending.extend(reversed(item.children))
    line = node.get('line')
    if isinstance(line, int):
        matches = [item for item in matches if item.start_point.row + 1 == line]
    end_line = node.get('endLine')
    if isinstance(end_line, int):
        matches = [item for item in matches if item.end_point.row + 1 == end_line]
    if len(matches) != 1:
        return None, 'Exact Swift declaration is missing or ambiguous; name/start range must identify one func/init.'
    return matches[0], None


def _documentation(declaration, content):
    """Use only adjacent own comments; type comments never become method documentation."""
    result = empty_documentation()
    comments, previous = [], declaration.prev_named_sibling
    next_line = declaration.start_point.row
    while previous is not None and previous.type in {'comment', 'multiline_comment'}:
        if previous.end_point.row < next_line - 1:
            break
        comments.append(_text(previous, content))
        next_line = previous.start_point.row
        previous = previous.prev_named_sibling
    text = '\n'.join(reversed(comments))
    text = re.sub(r'(?m)^\s*///?\s?', '', text)
    text = re.sub(r'^/\*\*?|\*/$', '', text).strip()
    result['summary'] = text[:8000]
    result['truncated'] = len(text) > 8000
    return result


def _declaration_facts(declaration, content):
    """Capture literal signature, internal parameter names and return type without binding."""
    body = declaration.child_by_field_name('body')
    signature_end = body.start_byte if body else declaration.end_byte
    signature = content[declaration.start_byte:signature_end].decode('utf-8').strip()
    parameters = []
    for child in declaration.named_children:
        if child.type != 'parameter':
            continue
        names = [part for part in child.named_children if part.type == 'simple_identifier']
        colon = next((part for part in child.children if part.type == ':'), None)
        parameter_type = content[colon.end_byte:child.end_byte].decode('utf-8').strip() if colon else ''
        parameters.append({'name': _text(names[-1], content) if names else '', 'type': parameter_type[:400]})
    arrow = next((child for child in declaration.children if child.type == '->'), None)
    return_type = ''
    if arrow:
        return_type = content[arrow.end_byte:signature_end].decode('utf-8').strip()
        return_type = return_type.split(' where ', 1)[0].strip()
    return {'signature': signature[:400], 'parameters': parameters[:40],
            'returnType': return_type[:400], 'documentation': _documentation(declaration, content),
            'declarationTruncated': len(signature) > 400 or len(parameters) > 40 or len(return_type) > 400}


def _empty():
    """Language-specific unavailable facts do not advertise C# parameter/attribute support."""
    reason = 'Swift detailed parameter usage and attributes are not extracted.'
    return {'kind': 'method', 'sourceStatus': 'unavailable', 'analyzerVersion': VERSION,
            'signature': '', 'parameters': [], 'returnType': '', 'documentation': empty_documentation(),
            'events': [], 'eventsTruncated': False, 'unresolved': [],
            'parameterFacts': {'kind': 'lexical-source', 'status': 'unavailable', 'parameters': [],
                               'conditions': [], 'truncated': False, 'warning': reason},
            'attributeFacts': {'kind': 'lexical-source', 'status': 'unavailable', 'groups': [],
                               'truncated': False, 'warning': reason},
            'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': []},
            'methodFlow': {'version': VERSION, 'status': 'unavailable', 'steps': [], 'truncated': False,
                           'unsupported': [], 'sourceKind': 'working-tree-ast',
                           'limits': SwiftFlow.limits(), 'warning': SwiftFlow.WARNING}}


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Parse one exact Swift func/init statically; unavailable grammars/errors never invent a tree."""
    result = _empty()
    result['methodFlow']['limits']['maxEvents'] = max(0, max_events)
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_swift
        content = source_bytes(source)
        tree = parsed_tree(source, 'swift', lambda data: Parser(Language(tree_sitter_swift.language())).parse(data))
    except (ImportError, ValueError, TypeError) as error:
        result['unresolved'].append('Local Swift grammar unavailable: ' + type(error).__name__)
        return result
    declaration, reason = _select(tree.root_node, content, node)
    if reason:
        result['sourceStatus'] = 'unresolved'
        result['unresolved'].append(reason)
        return result
    result.update(line=declaration.start_point.row + 1, endLine=declaration.end_point.row + 1)
    if declaration.has_error:
        result['sourceStatus'] = 'unresolved'
        result['unresolved'].append('Selected Swift declaration contains parse errors; detailed tree withheld.')
        return result
    result.update(_declaration_facts(declaration, content))
    result['sourceStatus'] = 'parsed'
    body = declaration.child_by_field_name('body')
    if body is None:
        result['methodFlow']['status'] = 'declaration-only'
        result['unresolved'].append('Swift declaration has no executable body.')
        return result
    builder = SwiftFlow(content, max_events)
    steps = builder.statements(body)
    result.update(events=builder.events, eventsTruncated=builder.events_truncated or builder.truncated,
                  unresolved=sorted(builder.unsupported))
    result['controlFlow'] = {'branches': builder.branches, 'terminals': builder.terminals,
                             'truncated': builder.truncated, 'unsupported': sorted(builder.unsupported)}
    result['methodFlow'].update(status='parsed', steps=steps, truncated=builder.truncated,
                                unsupported=sorted(builder.unsupported))
    return result
