"""Ограниченный статический разбор Java без исполнения исследуемого проекта."""
from .parsed_source import source_bytes
from ..node_documentation import empty_documentation
from .java_support.declarations import locate, declaration_facts, MAX_AST_NODES
from .java_support.flow import JavaFlow


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Возвращает точное объявление и дерево; неоднозначный исходник остаётся unresolved."""
    result = {
        'kind': 'method', 'line': node.get('line'), 'endLine': None,
        'sourceStatus': 'unavailable', 'analyzerVersion': 'java-method-flow-v1',
        'signature': '', 'parameters': [], 'returnType': '',
        'documentation': empty_documentation(), 'events': [], 'eventsTruncated': False,
        'unresolved': [], 'parameterFacts': {'status': 'unavailable', 'parameters': [],
            'truncated': False, 'warning': 'Java parameter binding is not implemented.'},
        'attributeFacts': {'status': 'unavailable', 'attributes': [], 'truncated': False,
            'warning': 'Java annotation facts are not implemented.'},
        'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': []},
        'methodFlow': {'version': 'java-method-flow-v1', 'status': 'unavailable',
            'steps': [], 'truncated': False, 'unsupported': [],
            'sourceKind': 'working-tree-ast',
            'limits': {'maxAstNodes': MAX_AST_NODES, 'maxSteps': 180, 'maxDepth': 24},
            'warning': 'Статические альтернативы Java; runtime и overload binding неизвестны.'},
    }
    content = source_bytes(source)
    declaration, reason, unavailable = locate(content, node, source=source)
    if declaration is None:
        result['sourceStatus'] = 'unavailable' if unavailable else 'unresolved'
        result['unresolved'].append(reason)
        return result
    result.update(declaration_facts(declaration, content))
    body = declaration.child_by_field_name('body')
    result['sourceStatus'] = 'parsed'
    if body is None:
        result['methodFlow']['status'] = 'declaration-only'
        result['unresolved'].append('Объявление Java не имеет тела.')
        return result
    flow = JavaFlow(content, max_events)
    steps = flow.statements(body)
    unsupported = sorted(flow.unsupported)
    result.update(events=flow.events, eventsTruncated=flow.events_truncated)
    result['unresolved'].extend(unsupported)
    result['methodFlow'].update(status='parsed', steps=steps, truncated=flow.truncated,
                                unsupported=unsupported)
    result['controlFlow'].update(branches=flow.branches, terminals=flow.terminals,
                                truncated=flow.truncated, unsupported=unsupported)
    return result
