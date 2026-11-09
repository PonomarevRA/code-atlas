"""Static, bounded C++ method bodies. Never compile, expand macros or execute source."""
from .parsed_source import parsed_tree, source_bytes
from ..node_documentation import empty_documentation
from .cpp_support.declarations import MAX_AST, walk, select, declaration_facts, text
from .cpp_support.flow import Flow

VERSION = 'cpp-method-flow-v1'


def empty_result(reason, status='unresolved'):
    """Return an explicit unsupported declaration instead of a plausible empty body."""
    return {'kind': 'method', 'sourceStatus': status, 'analyzerVersion': VERSION,
            'signature': '', 'parameters': [], 'returnType': '', 'documentation': empty_documentation(),
            'events': [], 'eventsTruncated': False, 'unresolved': [reason],
            'parameterFacts': {'kind': 'lexical-source', 'status': 'unavailable', 'parameters': [], 'conditions': [], 'truncated': False,
                               'warning': 'C++ parameter semantics are not inferred.'},
            'attributeFacts': {'kind': 'lexical-source', 'status': 'unavailable', 'groups': [], 'truncated': False,
                               'warning': 'C++ attribute semantics are not inferred.'},
            'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': [reason]},
            'methodFlow': {'version': VERSION, 'status': 'unavailable', 'steps': [], 'truncated': False,
                           'unsupported': [reason], 'sourceKind': 'working-tree-ast',
                           'limits': {'astNodes': MAX_AST, 'steps': 180, 'depth': 24}, 'warning': reason}}


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Return bounded lexical facts for node in host-read source['text'].

    The node's name/range/owner/parameter syntax must select one declaration.
    max_events caps retained call sites; no files or project code are loaded.
    Missing grammar and ambiguous/invalid syntax return explicit unavailable facts.
    """
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_cpp
    except ImportError:
        return empty_result('Installed C++ Tree-sitter grammar is unavailable.', 'unavailable')
    if node.get('_callable_class') or node.get('kind') in {'class', 'struct', 'namespace', 'file', 'module'}:
        return empty_result('C++ selected node is a type/module, not a callable body.')
    content = source_bytes(source)
    root = parsed_tree(source, 'cpp', lambda data: Parser(Language(tree_sitter_cpp.language())).parse(data)).root_node
    nodes = []
    for item in walk(root):
        if len(nodes) >= MAX_AST:
            result = empty_result('C++ AST node budget exceeded.')
            result['methodFlow']['truncated'] = True
            result['controlFlow']['truncated'] = True
            return result
        nodes.append(item)
    if root.has_error:
        return empty_result('C++ parse error or missing syntax; no reliable method tree.')
    declaration = select(nodes, node, content)
    if declaration is None:
        return empty_result('C++ declaration missing or ambiguous by name, range, owner or overload.')
    current = declaration.parent
    while current:
        if current.type.startswith('preproc_'):
            return empty_result('C++ declaration depends on unexpanded preprocessing.')
        current = current.parent
    macro_names = set()
    for item in nodes:
        if item.type in {'preproc_function_def', 'preproc_def'}:
            macro_names.add(text(item.child_by_field_name('name'), content))
    flow = Flow(content, max_events, macro_names)
    body = declaration.child_by_field_name('body')
    initializers = [c for c in declaration.named_children if c.type == 'field_initializer_list']
    steps = []
    if initializers:
        steps.extend(flow.opaque(initializers[0], 'Constructor member/base initialization order and implicit constructors are not resolved.'))
    steps.extend(flow.build(body))
    # Parent controls may hit the shared budget after extracting their children.
    # Keep events only for call rows that actually survived in the bounded tree.
    sites = set()
    pending = list(steps)
    while pending:
        step = pending.pop()
        if step['kind'] == 'call':
            sites.add(tuple(step['site'].values()))
        for arm in step.get('arms', []):
            pending.extend(arm['steps'])
        for field in ('condition', 'body', 'update'):
            pending.extend(step.get(field, []))
    flow.events = [event for event in flow.events if tuple(event[k] for k in ('line', 'column', 'endLine', 'endColumn')) in sites]
    result = empty_result('')
    result.update(declaration_facts(declaration, content))
    result.update(sourceStatus='parsed', line=declaration.start_point.row + 1,
                  endLine=declaration.end_point.row + 1, events=flow.events,
                  eventsTruncated=flow.events_truncated, unresolved=flow.unsupported[:])
    result['controlFlow'] = {'branches': flow.branches, 'terminals': flow.terminals,
                             'truncated': flow.truncated, 'unsupported': flow.unsupported[:]}
    result['methodFlow'].update(status='parsed', steps=steps, truncated=flow.truncated,
                                unsupported=flow.unsupported[:], warning=flow.warning)
    return result
