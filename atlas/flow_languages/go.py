"""Статический Go-адаптер: точное объявление и ограниченное дерево без исполнения."""
import re
from copy import deepcopy

from .parsed_source import parsed_tree
from ..node_documentation import empty_documentation
from .go_support.flow import FlowBuilder, MAX_AST_NODES, MAX_DEPTH, MAX_STEPS

VERSION = 'go-method-flow-v1'


def _text(node):
    """Читает текст уже переданного AST, не обращаясь к файловой системе."""
    return node.text.decode('utf-8') if node is not None else ''


def _empty(node, reason, status='unavailable'):
    """Возвращает честный fallback и переопределяет C#-специфичные метаданные host."""
    return {
        'kind': 'method', 'line': node.get('line'), 'endLine': None,
        'sourceStatus': status, 'analyzerVersion': VERSION,
        'signature': '', 'parameters': [], 'returnType': '',
        'documentation': empty_documentation(), 'events': [], 'eventsTruncated': False,
        'callableFacts': {**deepcopy(node.get('callableFacts') or {}), 'controlFlowSupported': False,
                          'controlFlowSourceKind': 'working-tree-ast'},
        'unresolved': [reason] if reason else [],
        'parameterFacts': {'status': 'unavailable', 'parameters': [], 'truncated': False,
                           'warning': 'Go: detailed parameter binding is not implemented.'},
        'attributeFacts': {'status': 'unavailable', 'attributes': [], 'truncated': False,
                           'warning': 'Go has no C# attribute syntax; directives are not interpreted.'},
        'readabilityRecommendations': {'status': 'unavailable', 'items': [], 'truncated': False,
                                       'warning': 'Go return readability heuristics are not implemented.'},
        'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': []},
        'methodFlow': {'version': VERSION, 'status': 'unavailable', 'steps': [],
                       'truncated': False, 'unsupported': [reason] if reason else [],
                       'sourceKind': 'working-tree-ast',
                       'limits': {'maxAstNodes': MAX_AST_NODES, 'maxSteps': MAX_STEPS, 'maxDepth': MAX_DEPTH},
                       'warning': 'Static Go syntax only; scheduling, binding and runtime outcomes are unknown.'},
    }


def _declaration(root, node):
    """Выбирает имя с точной строкой; неоднозначный receiver никогда не угадывается."""
    name = (node.get('memberFacts') or {}).get('name')
    if not name:
        name = str(node.get('label') or '').split('(', 1)[0].strip('.').rsplit('.', 1)[-1]
    matches = []
    pending = [root]
    while pending:
        current = pending.pop()
        if current.type in {'function_declaration', 'method_declaration'}:
            if _text(current.child_by_field_name('name')) == name:
                matches.append(current)
            # Go declarations cannot contain named method/function declarations.
            continue
        pending.extend(reversed(current.named_children))
    line = node.get('line')
    if isinstance(line, int):
        matches = [item for item in matches if item.start_point.row + 1 == line]
    end_line = node.get('endLine')
    if isinstance(end_line, int):
        matches = [item for item in matches if item.end_point.row + 1 == end_line]
    signature = (node.get('callableFacts') or {}).get('signature', '')
    owner = (node.get('callableFacts') or {}).get('declaringType', '')
    if owner and not owner.endswith('.go'):
        owned = []
        for item in matches:
            receiver = item.child_by_field_name('receiver')
            parameter = receiver.named_children[0] if receiver and receiver.named_children else None
            receiver_type = _text(parameter.child_by_field_name('type')) if parameter else ''
            receiver_name = receiver_type.lstrip('*').split('[', 1)[0]
            if receiver_name == owner:
                owned.append(item)
        matches = owned
    if len(matches) > 1 and signature.startswith('func '):
        matches = [item for item in matches if _text(item).split('{', 1)[0].strip() == signature.strip()]
    return matches[0] if len(matches) == 1 else None


def _parameters(declaration):
    """Сохраняет имена и буквальные типы, включая группы и variadic-параметры."""
    result = []
    parameters = declaration.child_by_field_name('parameters')
    for item in parameters.named_children if parameters is not None else []:
        type_text = _text(item.child_by_field_name('type'))
        if item.type == 'variadic_parameter_declaration':
            type_text = '...' + type_text
        names = item.children_by_field_name('name')
        for name in names or [None]:
            result.append({'name': _text(name), 'type': type_text[:400]})
    return result


def _documentation(declaration):
    """Захватывает только прилегающие собственные Go-комментарии до 8000 символов."""
    result = empty_documentation()
    current, next_row = declaration.prev_named_sibling, declaration.start_point.row
    comments = []
    count = 0
    while current is not None and current.type == 'comment' and current.end_point.row >= next_row - 1:
        text = _text(current)
        count += len(text)
        comments.append(text)
        if count > 8000:
            result['truncated'] = True
            break
        next_row = current.start_point.row
        current = current.prev_named_sibling
    text = '\n'.join(reversed(comments))[:8000]
    result['summary'] = re.sub(r'(?m)^\s*(?:// ?|/\* ?|\* ?)|\*/$', '', text).strip()
    return result


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Разбирает только локально установленной грамматикой и возвращает bounded факты."""
    result = _empty(node, '')
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_go
    except ImportError:
        return _empty(node, 'Go Tree-sitter grammar is unavailable.')
    tree = parsed_tree(source, 'go', lambda data: Parser(Language(tree_sitter_go.language())).parse(data))
    pending, count = [tree.root_node], 0
    while pending:
        current = pending.pop()
        count += 1
        if count > MAX_AST_NODES:
            result = _empty(node, 'Go AST node budget exceeded.')
            result['methodFlow']['truncated'] = result['controlFlow']['truncated'] = True
            return result
        pending.extend(current.named_children)
    if tree.root_node.has_error:
        return _empty(node, 'Go parse error; no reliable method tree was extracted.', 'unresolved')
    declaration = _declaration(tree.root_node, node)
    if declaration is None:
        return _empty(node, 'Exact Go declaration is absent or ambiguous; adjacent methods are excluded.', 'unresolved')
    body = declaration.child_by_field_name('body')
    signature = declaration.text[:body.start_byte - declaration.start_byte].decode('utf-8') if body else _text(declaration)
    result.update(sourceStatus='parsed', line=declaration.start_point.row + 1,
                  endLine=declaration.end_point.row + 1, signature=signature.strip()[:400],
                  declarationTruncated=len(signature.strip()) > 400,
                  parameters=_parameters(declaration), returnType=_text(declaration.child_by_field_name('result'))[:400],
                  documentation=_documentation(declaration))
    if body is None:
        result['methodFlow']['status'] = 'declaration-only'
        result['unresolved'].append('Go declaration has no body (implementation may be external).')
        return result
    builder = FlowBuilder(max_events=max_events)
    steps = builder.extract(body)
    result.update(events=builder.events, eventsTruncated=builder.events_truncated,
                  unresolved=list(builder.unsupported), controlFlow=builder.control_flow())
    result['methodFlow'].update(status='parsed', steps=steps, truncated=builder.truncated,
                                unsupported=list(builder.unsupported))
    result['callableFacts']['controlFlowSupported'] = True
    return result
