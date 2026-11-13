"""JavaScript/JSX-адаптер подробного статического дерева выбранного объявления."""
from .parsed_source import MAX_REQUEST_AST_NODES, parsed_tree, source_bytes
from ..node_documentation import empty_documentation
from .javascript_support.declarations import CALLABLES, anchor_lines, documentation, name_of, parameters, select
from .javascript_support.flow import Flow, site

ANALYZER_VERSION = 'javascript-method-flow-v1'
MAX_AST_NODES = 20000


def unavailable(reason, status='unresolved'):
    """Возвращает честную недоступность дерева без C#-специфичных обещаний."""
    flow = Flow(b'', 0).model([])
    flow.update(status='unavailable', unsupported=[reason])
    return {'kind': 'method', 'sourceStatus': status, 'analyzerVersion': ANALYZER_VERSION,
            'line': None, 'endLine': None, 'signature': '', 'parameters': [], 'returnType': 'unknown',
            'documentation': empty_documentation(), 'events': [], 'eventsTruncated': False,
            'methodFlow': flow, 'unresolved': [reason],
            'parameterFacts': {'kind': 'lexical-source', 'status': 'unavailable', 'parameters': [],
                               'conditions': [], 'truncated': False,
                               'warning': 'JavaScript parameter dataflow не извлечён; parameters содержит только bindings.'},
            'attributeFacts': {'kind': 'lexical-source', 'status': 'unavailable', 'groups': [],
                               'truncated': False, 'warning': 'C# attributes не применимы к JavaScript.'},
            'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': [reason]}}


def _declaration_branches(root, line):
    """Walk branches crossing a line, including direct callable values on later binding lines."""
    pending = [root]
    while pending:
        item = pending.pop()
        yield item
        for child in reversed(item.named_children):
            if line is None or child.start_point.row + 1 <= line <= child.end_point.row + 1:
                pending.append(child)
            elif child.type in CALLABLES and line in anchor_lines(child):
                pending.append(child)


def callable_declarations(source: dict, *, line=None):
    """Find the line's callable declarations within a bounded traversal of the parsed file."""
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
        content = source_bytes(source)
        root = parsed_tree(source, 'javascript', lambda data: Parser(Language(tree_sitter_javascript.language())).parse(data),
                           max_retained_nodes=MAX_REQUEST_AST_NODES).root_node
    except (ImportError, TypeError, ValueError) as error:
        return b'', None, unavailable(f'Локальная грамматика JavaScript недоступна: {type(error).__name__}.', 'unavailable')
    if root.has_error:
        return content, None, unavailable('JavaScript parse error: точное дерево недоступно.')
    declarations = []
    for count, item in enumerate(_declaration_branches(root, line), 1):
        if count > MAX_AST_NODES:
            result = unavailable(f'Исчерпан бюджет обхода JavaScript AST ({MAX_AST_NODES} узлов).', 'unavailable')
            result['methodFlow']['truncated'] = result['controlFlow']['truncated'] = True
            return content, None, result
        if item.type in CALLABLES:
            declarations.append(item)
    return content, declarations, None


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Разбирает выбранное объявление JavaScript, включая его локальный поток управления."""
    content, declarations, error = callable_declarations(source, line=node.get('line') or node.get('source_line'))
    if error is not None:
        return error
    declaration = select(declarations, node, content)
    if declaration is None:
        return unavailable('Выбранное JavaScript-объявление не найдено однозначно по имени, строке и владельцу.')
    result = unavailable('')
    body = declaration.child_by_field_name('body')
    signature_end = body.start_byte if body is not None else declaration.end_byte
    result.update(sourceStatus='parsed', line=declaration.start_point.row + 1, endLine=declaration.end_point.row + 1,
                  column=declaration.start_point.column + 1, endColumn=declaration.end_point.column + 1,
                  signature=content[declaration.start_byte:signature_end].decode('utf-8').strip()[:400],
                  parameters=parameters(declaration, content), documentation=documentation(declaration, content))
    if declaration.type == 'method_definition' and name_of(declaration, content) == 'constructor':
        result['kind'] = 'constructor'
    flow = Flow(content, max_events)
    steps = []
    is_generator = declaration.type in {'generator_function', 'generator_function_declaration'}
    is_generator |= any(child.type == '*' for child in declaration.children)
    if is_generator:
        steps = flow.opaque(declaration, 'Generator body deferred: yield/resumption требует отдельной модели.')
    else:
        group = declaration.child_by_field_name('parameters')
        # Defaults execute only when the argument is undefined; destructuring is left opaque.
        for parameter in group.named_children if group else []:
            if parameter.type == 'assignment_pattern':
                right = parameter.child_by_field_name('right')
                steps.extend(flow.branch(parameter, 'parameter-default',
                    [('аргумент undefined', right, True), ('аргумент передан: skip', None, False)], (), 0))
                left = parameter.child_by_field_name('left')
                if left is not None and left.type in {'object_pattern', 'array_pattern'}:
                    steps.extend(flow.opaque(left, 'Destructuring parameter: binding/default effects не раскрыты.'))
            elif parameter.type in {'object_pattern', 'array_pattern'}:
                steps.extend(flow.opaque(parameter, 'Destructuring parameter: binding/default effects не раскрыты.'))
        steps.extend(flow.read(body))
        if body is not None and body.type != 'statement_block' and not flow.truncated:
            terminal = flow.step('return', body, label='return ' + flow.label(body), implicit=True, implicitReturn=True)
            if terminal:
                steps.append(terminal)
                flow.terminals.append({'kind': 'return', **site(body), 'branchIds': []})
    result.update(events=flow.events, eventsTruncated=flow.events_truncated, methodFlow=flow.model(steps),
                  unresolved=list(flow.unsupported),
                  controlFlow={'branches': flow.branches, 'terminals': flow.terminals,
                               'truncated': flow.truncated, 'unsupported': list(flow.unsupported)})
    if is_generator:
        result['methodFlow']['status'] = 'declaration-only'
    result['parameterFacts']['warning'] = 'JavaScript: только буквальные bindings в parameters; dataflow и runtime-типы не установлены.'
    if flow.events_truncated:
        result['unresolved'].append('Список событий неполон; границы сокращения указаны в methodFlow.')
    return result
