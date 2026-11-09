"""Python static method trees from the standard ast; analyzed code is never executed."""
import ast

from .parsed_source import parsed_tree
from ..node_documentation import empty_documentation
from .python_support.source import Source, declarations, resolve, has_yield
from .python_support.flow import Flow

VERSION = 'python-method-flow-v1'


def _empty(reason, status='unresolved'):
    """Return an explicit unavailable model instead of another declaration's body."""
    unsupported = [reason] if reason else []
    facts_warning = 'Python detailed parameter/attribute facts are not extracted.'
    return {'kind': 'unknown', 'line': None, 'endLine': None, 'sourceStatus': status,
            'analyzerVersion': VERSION, 'signature': '', 'parameters': [], 'returnType': '',
            'documentation': empty_documentation(), 'typeContext': None,
            'parameterFacts': {'status': 'unavailable', 'parameters': [], 'truncated': False, 'warning': facts_warning},
            'attributeFacts': {'status': 'unavailable', 'attributes': [], 'truncated': False, 'warning': facts_warning},
            'events': [], 'eventsTruncated': False, 'unresolved': unsupported.copy(),
            'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': unsupported.copy()},
            'methodFlow': {'version': VERSION, 'status': 'unavailable', 'steps': [],
                           'truncated': False, 'unsupported': unsupported.copy(),
                           'sourceKind': 'working-tree-ast',
                           'limits': {'astNodes': 20000, 'steps': 180, 'depth': 24},
                           'warning': 'Static lexical alternatives; not an execution trace.'}}


def _documentation(declaration):
    """Keep only this declaration's own docstring, bounded independently of the type context."""
    result = empty_documentation()
    text = ast.get_docstring(declaration, clean=False) or ''
    result.update(summary=text[:8000], truncated=len(text) > 8000)
    return result


def _declaration_facts(source, declaration, owners):
    """Extract annotation text and own documentation without evaluating defaults or decorators."""
    parameters = []
    arguments = getattr(declaration, 'args', None)
    if arguments:
        ordered = [*arguments.posonlyargs, *arguments.args]
        if arguments.vararg:
            ordered.append(arguments.vararg)
        ordered.extend(arguments.kwonlyargs)
        if arguments.kwarg:
            ordered.append(arguments.kwarg)
        for arg in ordered:
            parameters.append({'name': arg.arg, 'type': source.fragment(arg.annotation)})
    kind = 'class' if isinstance(declaration, ast.ClassDef) else 'method'
    signature, signature_truncated = source.header(declaration)
    context = next((o for o in reversed(owners) if isinstance(o, ast.ClassDef)), None)
    return {'kind': kind, 'line': declaration.lineno, 'endLine': declaration.end_lineno,
            'sourceStatus': 'parsed', 'signature': signature[:400], 'parameters': parameters,
            'declarationTruncated': signature_truncated,
            'returnType': source.fragment(getattr(declaration, 'returns', None)),
            'documentation': _documentation(declaration),
            'typeContext': {'name': context.name, 'kind': 'class', 'line': context.lineno,
                            'documentation': _documentation(context)} if context else None}


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Resolve one declaration and return bounded facts without reading or importing any file."""
    result = _empty('')
    text = source.get('text', '')
    try:
        tree = parsed_tree(source, 'python', lambda content: ast.parse(text))
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        return _empty('Python ast parse unavailable: ' + type(error).__name__, 'unavailable')
    found, limited = declarations(tree)
    if limited:
        result = _empty('Python AST node budget exceeded (20000).', 'unavailable')
        result['methodFlow']['truncated'] = result['controlFlow']['truncated'] = True
        return result
    selected = resolve(found, node)
    if selected is None:
        return _empty('Exact Python declaration is missing or ambiguous; neighboring bodies are excluded.')
    declaration, owners = selected
    reader = Source(text)
    result.update(_declaration_facts(reader, declaration, owners))
    flow = Flow(reader, max_events)
    if isinstance(declaration, ast.ClassDef):
        steps = []
        result['methodFlow']['status'] = 'declaration-only'
        flow.unsupported.add('Class body is a declaration context, not a method invocation.')
    elif has_yield(declaration):
        steps = flow.opaque(declaration, 'Generator suspension/resumption and yield-from are not represented.')
        result['methodFlow']['status'] = 'declaration-only'
    else:
        steps = flow.statements(declaration.body)
        result['methodFlow']['status'] = 'parsed'
    unsupported = sorted(flow.unsupported)
    result.update(events=flow.events, eventsTruncated=flow.events_truncated or flow.truncated,
                  unresolved=unsupported.copy())
    if flow.truncated:
        result['unresolved'].append('Python tree/events shortened by the shared step, depth or event budget.')
    result['controlFlow'].update(branches=flow.branches, terminals=flow.terminals,
                                 truncated=flow.truncated, unsupported=unsupported)
    result['methodFlow'].update(steps=steps, truncated=flow.truncated,
                                unsupported=unsupported, limits={**result['methodFlow']['limits'], 'events': max_events})
    return result
