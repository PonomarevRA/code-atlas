"""Static TypeScript/TSX flow adapter; parses source bytes without evaluating project code."""
from pathlib import Path

from .parsed_source import parsed_tree, source_bytes, MAX_REQUEST_AST_NODES, MAX_REQUEST_AST_SOURCE_BYTES
from atlas.node_documentation import empty_documentation
from atlas.source_readability import empty_readability_recommendations
from .typescript_support.source import select, declaration_facts, declaration_candidates, MAX_AST_NODES, MAX_SELECTION_NODES
from .typescript_support.flow import Flow, VERSION, MAX_STEPS, MAX_DEPTH


def unavailable(reason, status='unresolved'):
    """Provide honest unavailable facts instead of inheriting C#-specific capabilities."""
    unsupported = [reason] if reason else []
    recommendations = empty_readability_recommendations()
    recommendations.update(reason='unsupported-language', language='typescript',
                           warning='TypeScript readability recommendation rules are not implemented; empty items do not indicate a passed check.')
    return dict(kind='unknown', sourceStatus=status, analyzerVersion=VERSION, line=None, endLine=None,
                signature='', parameters=[], returnType='', documentation=empty_documentation(),
                readabilityRecommendations=recommendations,
                events=[], eventsTruncated=False, unresolved=unsupported,
                parameterFacts=dict(status='unavailable', parameters=[], truncated=False, warning='TypeScript parameters are lexical metadata only.'),
                attributeFacts=dict(status='unavailable', truncated=False, warning='TypeScript decorators are not analyzed as method execution.'),
                controlFlow=dict(branches=[], terminals=[], truncated=False, unsupported=unsupported),
                methodFlow=dict(version=VERSION, status='unavailable', steps=[], truncated=False,
                                unsupported=unsupported, sourceKind='working-tree-ast',
                                limits=dict(maxAstNodes=MAX_AST_NODES, maxSelectionAstNodes=MAX_SELECTION_NODES,
                                            maxRetainedAstNodes=MAX_REQUEST_AST_NODES,
                                            maxRetainedAstSourceBytes=MAX_REQUEST_AST_SOURCE_BYTES,
                                            maxSteps=MAX_STEPS, maxDepth=MAX_DEPTH), warning=reason))


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Resolve one typed declaration and return bounded lexical facts, never raw graph identities."""
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_typescript
    except ImportError:
        return unavailable('Installed TypeScript Tree-sitter grammar is unavailable.', 'unavailable')
    filename = source.get('file') or node.get('file') or ''
    content = source_bytes(source)
    grammar_id = 'tsx' if Path(filename).suffix.lower() == '.tsx' else 'typescript'
    try:
        grammar = tree_sitter_typescript.language_tsx if grammar_id == 'tsx' else tree_sitter_typescript.language_typescript
        tree = parsed_tree(source, grammar_id, lambda data: Parser(Language(grammar())).parse(data),
                           max_retained_nodes=MAX_REQUEST_AST_NODES)
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        result = unavailable(f'TypeScript parser failed ({type(error).__name__}); no execution tree was inferred.', 'unavailable')
        result['methodFlow'].update(status='error', reason='parser-error')
        return result
    nodes, limited = declaration_candidates(tree.root_node, node)
    if limited:
        result = unavailable('TypeScript declaration selection exceeds the 20000-node budget; identity is not proven.')
        result['methodFlow'].update(truncated=True, reason='selection-ast-budget')
        result['declarationTruncated'] = True
        result['controlFlow']['truncated'] = True
        return result
    declaration = select(nodes, node, content, filename)
    if declaration is None:
        return unavailable('Exact TypeScript declaration is missing or ambiguous; neighbouring methods are not used.')
    if declaration.has_error:
        return unavailable('Selected TypeScript declaration contains parse errors; no execution tree was inferred.')
    if declaration.descendant_count > MAX_AST_NODES:
        result = unavailable('Selected TypeScript declaration exceeds the 20000-node body budget.')
        result['methodFlow'].update(truncated=True, reason='body-ast-budget')
        result['declarationTruncated'] = True
        result['controlFlow']['truncated'] = True
        return result
    flow = Flow(content, max_events)
    method_flow = flow.build(declaration)
    method_flow['limits'].update(maxSelectionAstNodes=MAX_SELECTION_NODES,
                                maxRetainedAstNodes=MAX_REQUEST_AST_NODES,
                                maxRetainedAstSourceBytes=MAX_REQUEST_AST_SOURCE_BYTES)
    result = unavailable('')
    result.update(declaration_facts(declaration, content))
    result.update(kind='method', sourceStatus='parsed', line=declaration.start_point.row + 1,
                  endLine=declaration.end_point.row + 1, events=flow.events,
                  eventsTruncated=flow.events_truncated, methodFlow=method_flow,
                  unresolved=list(flow.unsupported),
                  controlFlow=dict(branches=flow.branches, terminals=flow.terminals,
                                   truncated=flow.truncated, unsupported=list(flow.unsupported)))
    if flow.truncated:
        result['unresolved'].append('TypeScript method tree was truncated by its step/depth budget.')
    if flow.events_truncated:
        result['unresolved'].append('TypeScript call events were truncated by max_events.')
    return result
