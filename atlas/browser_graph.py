"""Build a temporary DOM/JavaScript evidence graph without executing or caching site code."""
from hashlib import sha256

from .flow_languages.javascript_support.declarations import CALLABLES, name_of

MAX_AST_NODES = 20_000
MAX_FUNCTIONS = 1000
MAX_EDGES = 8000


def build_browser_graph(capture):
    """Project captured DOM and bounded lexical JS facts; all dynamic bindings remain unknown."""
    nodes, edges, sources = {}, [], {}
    warnings = []
    for item in capture.nodes:
        identifier = 'dom:' + item.id
        attrs = dict(item.attributes)
        label = item.tag + ('#' + attrs['id'] if attrs.get('id') else '')
        nodes[identifier] = {'id': identifier, 'kind': 'dom', 'label': label[:256],
                             'text': item.text, 'attributes': attrs, 'bounds': item.bounds,
                             'styles': item.styles, 'evidence': 'browser-dom-observation'}
        if item.parentId is not None:
            edges.append({'source': 'dom:' + item.parentId, 'target': identifier,
                          'relation': 'contains', 'evidence': 'browser-dom-observation'})
    budget = {'ast': MAX_AST_NODES, 'functions': MAX_FUNCTIONS}
    for script in capture.scripts:
        identifier = 'script:' + script.id
        nodes[identifier] = {'id': identifier, 'kind': 'script', 'scriptId': script.id,
                             'label': script.url[:256] or '<inline>', 'status': script.status,
                             'evidence': 'browser-script-inventory'}
        if script.source is None:
            continue
        source = script.source
        digest = sha256(source.encode('utf-8')).hexdigest()
        sources[script.id] = source
        nodes[identifier].update(sourceHash=digest, sourceLength=len(source))
        nodes[identifier]['analysisStatus'] = _script_graph(script.id, source, nodes, edges, warnings, budget)
    # Host diagnostics precede caller warnings, so untrusted input cannot hide a parser failure.
    combined = list(dict.fromkeys(warnings + capture.warnings))
    if len(combined) > 32:
        combined = combined[:31] + ['analysis-or-capture-warnings-truncated']
    return {'nodes': nodes, 'edges': edges, 'sources': sources,
            'warnings': combined,
            'runtimeSelectionKnown': False,
            'scope': 'top-frame observation and bounded lexical syntax; no execution trace'}


def _script_graph(script_id, source, nodes, edges, warnings, budget):
    """Analyze one installed grammar, sharing total AST/declaration budgets across scripts."""
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
    except ImportError:
        warnings.append('javascript-parser-unavailable')
        return 'parser-unavailable'
    if budget['ast'] <= 0 or budget['functions'] <= 0:
        warnings.append('javascript-analysis-budget')
        return 'budget-exhausted'
    encoded = source.encode('utf-8')
    root = Parser(Language(tree_sitter_javascript.language())).parse(encoded).root_node
    if root.has_error:
        warnings.append('javascript-parse-error:' + script_id)
        return 'parse-error'
    pending, calls, names = [(root, 'script:' + script_id)], [], {}
    while pending and budget['ast'] > 0 and budget['functions'] > 0:
        current, owner_id = pending.pop()
        budget['ast'] -= 1
        if current.type in CALLABLES:
            name = name_of(current, encoded) or '<anonymous>'
            identifier = f'js:{script_id}:{current.start_byte}'
            nodes[identifier] = {'id': identifier, 'kind': 'function', 'label': name[:256],
                                 'scriptId': script_id, 'line': current.start_point.row + 1,
                                 'startByte': current.start_byte, 'endByte': current.end_byte,
                                 'evidence': 'lexical-declaration', 'runtimeSelectionKnown': False}
            owner_id = identifier
            names.setdefault(name, []).append(identifier)
            budget['functions'] -= 1
            edges.append({'source': 'script:' + script_id, 'target': identifier,
                          'relation': 'declares', 'evidence': 'lexical-declaration'})
        elif current.type == 'call_expression':
            calls.append((current, owner_id))
        # Carry lexical ownership once. Repeated Tree-sitter parent walks on
        # deeply nested call chains otherwise turn a small file into quadratic work.
        for child in reversed(current.named_children):
            pending.append((child, owner_id))
    if pending:
        warnings.append('javascript-analysis-budget')
    for call, source_id in calls:
        if len(edges) >= MAX_EDGES:
            warnings.append('javascript-edge-budget')
            break
        function = call.child_by_field_name('function')
        # Only exact short callee names are interpreted; never copy a whole nested chain per call.
        expression = ''
        if function is not None and function.end_byte - function.start_byte <= 128:
            expression = encoded[function.start_byte:function.end_byte].decode('utf-8')
        if expression in {'fetch', 'document.querySelector', 'document.querySelectorAll', 'document.getElementById'}:
            facts = nodes[source_id].setdefault('lexicalFacts', [])
            arguments = call.child_by_field_name('arguments')
            if len(facts) < 8 and arguments is not None and arguments.named_child_count:
                argument = arguments.named_child(0)
                facts.append({'kind': 'request-expression' if expression == 'fetch' else 'dom-selector-expression',
                              'callee': expression, 'argumentExpression': encoded[argument.start_byte:min(argument.end_byte, argument.start_byte + 512)].decode('utf-8', errors='ignore')[:128],
                              'line': call.start_point.row + 1, 'runtimeSelectionKnown': False})
        # A name match cannot resolve lexical shadowing, imports or runtime dispatch.
        if function is not None and function.type == 'identifier':
            name = expression
            if len(names.get(name, [])) > 8:
                warnings.append('javascript-call-candidates-truncated')
            for target in names.get(name, [])[:8]:
                if len(edges) >= MAX_EDGES:
                    warnings.append('javascript-edge-budget')
                    break
                edges.append({'source': source_id, 'target': target, 'relation': 'call_candidate',
                              'evidence': 'same-script-name-match', 'line': call.start_point.row + 1,
                              'runtimeSelectionKnown': False})
    return 'partial' if pending or len(edges) >= MAX_EDGES else 'parsed-lexical'
