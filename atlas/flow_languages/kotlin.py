"""Bounded Kotlin method trees from local Tree-sitter syntax, without execution."""
from atlas.attribute_facts import empty_attribute_facts
from atlas.parameter_facts import empty_parameter_facts
from atlas.node_documentation import empty_documentation
from .parsed_source import parsed_tree, source_bytes
from .kotlin_support.declarations import select, declaration_facts, body_of
from .kotlin_support.flow import KotlinFlow, VERSION


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Analyze only an unambiguous selected declaration in the safely supplied source."""
    parameters = empty_parameter_facts()
    parameters.update(status='unavailable', warning='Kotlin parameter usage/dataflow facts are not implemented; explicit declaration types are listed separately.')
    attributes = empty_attribute_facts()
    attributes['warning'] = 'Kotlin annotations are not extracted by this adapter.'
    result = {'kind': 'method', 'line': node.get('line'), 'endLine': None,
              'sourceStatus': 'unavailable', 'analyzerVersion': VERSION, 'signature': '',
              'parameters': [], 'returnType': '', 'documentation': empty_documentation(),
              'parameterFacts': parameters, 'attributeFacts': attributes, 'events': [],
              'eventsTruncated': False, 'unresolved': [],
              'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': []},
              'methodFlow': {'version': VERSION, 'status': 'unavailable', 'steps': [],
                             'truncated': False, 'unsupported': [], 'sourceKind': 'working-tree-ast',
                             'limits': {'maxAstNodes': 20000, 'maxSteps': 180, 'maxDepth': 24, 'maxEvents': max(0, max_events)},
                             'warning': 'Static lexical evaluation only. Suspend calls, coroutine scheduling, implicit operator/accessor calls and overload binding are not resolved.'}}
    if node.get('kind') in {'class', 'interface', 'object', 'file', 'field', 'external'} or node.get('_callable_class'):
        result['kind'] = node.get('kind', 'class')
        result['unresolved'].append('Kotlin detailed flow requires a callable declaration node.')
        return result
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_kotlin
        content = source_bytes(source)
        tree = parsed_tree(source, 'kotlin', lambda data: Parser(Language(tree_sitter_kotlin.language())).parse(data))
    except (ImportError, AttributeError, TypeError, ValueError) as error:
        result['unresolved'].append('Local Kotlin grammar unavailable: ' + type(error).__name__)
        return result
    if tree.root_node.has_error:
        result['sourceStatus'] = 'unresolved'
        result['unresolved'].append('Kotlin parse error; a detailed tree is not emitted.')
        return result
    declaration, reason = select(tree.root_node, node, content)
    if reason:
        result['sourceStatus'] = 'unresolved'
        result['unresolved'].append(reason)
        if '20000' in reason:
            result['methodFlow']['truncated'] = True
        return result
    result.update(declaration_facts(declaration, content), sourceStatus='parsed',
                  line=declaration.start_point.row + 1, endLine=declaration.end_point.row + 1)
    body = body_of(declaration)
    if body is None:
        result['methodFlow']['status'] = 'declaration-only'
        result['unresolved'].append('Kotlin declaration has no implementation body.')
        return result
    flow = KotlinFlow(content, max_events, node.get('id') or source.get('file') or 'selected')
    steps = flow.declaration(declaration)
    unsupported = sorted(flow.unsupported)
    result.update(events=flow.events, eventsTruncated=flow.events_truncated,
                  controlFlow={'branches': flow.branches, 'terminals': flow.terminals,
                               'truncated': flow.truncated, 'unsupported': unsupported})
    result['methodFlow'].update(status='parsed', steps=steps, truncated=flow.truncated, unsupported=unsupported)
    result['unresolved'].extend(unsupported)
    if flow.truncated:
        result['unresolved'].append('Kotlin method tree reached its step/depth budget.')
    if flow.events_truncated:
        result['unresolved'].append('Kotlin call events reached max_events; omitted calls remain explicit incomplete source steps.')
    return result
