"""Static Rust method bodies through the shared safe-source host, without rustc."""
from .parsed_source import parsed_tree, source_bytes
from ..node_documentation import empty_documentation
from .rust_support import syntax
from .rust_support.flow import MAX_DEPTH, MAX_STEPS, RustFlow

VERSION = 'rust-method-flow-v1'


def _empty(reason, status='unavailable'):
    """Return explicit unavailable Rust facts, including unsupported contract panels."""
    return {'kind': 'method', 'sourceStatus': status, 'analyzerVersion': VERSION,
            'signature': '', 'parameters': [], 'returnType': '', 'documentation': empty_documentation(),
            'events': [], 'eventsTruncated': False, 'unresolved': [reason],
            'parameterFacts': {'status': 'unavailable', 'parameters': [], 'conditions': [], 'truncated': False, 'warning': 'Rust parameter contract details are not extracted.'},
            'attributeFacts': {'status': 'unavailable', 'groups': [], 'attributes': [], 'truncated': False, 'warning': 'Rust attributes/cfg are not evaluated.'},
            'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': [reason]},
            'methodFlow': {'version': VERSION, 'status': 'unavailable', 'steps': [], 'truncated': False,
                           'unsupported': [reason], 'sourceKind': 'working-tree-ast',
                           'limits': {'astNodes': syntax.MAX_AST_NODES, 'steps': MAX_STEPS, 'depth': MAX_DEPTH,
                                      'textChars': syntax.MAX_TEXT, 'documentationChars': 8000},
                           'warning': 'Lexical Rust source only; trait dispatch, panic/drop cleanup and async runtime are unknown.'}}


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Parse only host-provided text and select one declaration with bounded AST work."""
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_rust
        content = source_bytes(source)
        tree = parsed_tree(source, 'rust', lambda data: Parser(Language(tree_sitter_rust.language())).parse(data))
    except (ImportError, AttributeError, TypeError, ValueError) as error:
        return _empty('Installed Rust grammar unavailable: ' + type(error).__name__)
    declarations, truncated = syntax.scan(tree.root_node)
    if truncated:
        result = _empty('Rust AST node budget exceeded (20000).')
        result['methodFlow']['truncated'] = result['controlFlow']['truncated'] = True
        return result
    declaration, reason = syntax.select(declarations, node, content)
    if declaration is None:
        return _empty(reason, 'unresolved')
    if tree.root_node.has_error:
        return _empty('Rust parse error; detailed tree is unavailable for this file.')
    result = _empty('')
    result['methodFlow']['limits']['events'] = max(0, max_events)
    result.update(syntax.facts(declaration, content), sourceStatus='parsed', unresolved=[])
    result['controlFlow']['unsupported'] = []
    body = declaration.child_by_field_name('body')
    if body is None:
        reason = 'Rust trait declaration has no body; implementation/dispatch are unknown.'
        result['unresolved'] = [reason]
        result['controlFlow']['unsupported'] = [reason]
        result['methodFlow'].update(status='declaration-only', unsupported=[reason])
        return result
    flow = RustFlow(content, max_events=max_events)
    steps = flow.block(body, (), 0)
    # A final expression returns its value implicitly; do not invent a second call.
    children = [c for c in body.named_children if c.type not in {'line_comment', 'block_comment', 'attribute_item'}]
    if children and flow.falls_through(steps):
        last = children[-1]
        if last.type == 'expression_statement' and not syntax.text(last, content).rstrip().endswith(';'):
            last = last.named_children[0]
        if last.type.endswith(('_expression', '_literal')) or last.type in {'identifier', 'block'}:
            steps.extend(flow.emit('return', last, label='implicit return: ' + syntax.text(last, content)[:360], implicit=True, implicitReturn=True, reuseExpressionResult=True))
    result.update(events=flow.events, eventsTruncated=flow.events_truncated, unresolved=list(flow.unsupported))
    result['controlFlow'].update(branches=flow.branches, terminals=flow.terminals,
                                 truncated=flow.truncated, unsupported=list(flow.unsupported))
    result['methodFlow'].update(status='parsed', steps=steps, truncated=flow.truncated,
                                unsupported=list(flow.unsupported))
    return result
