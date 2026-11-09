"""Capture Python HTTP/MCP registrations once per map, with explicit source and analysis budgets."""
import ast
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
from pathlib import Path, PurePosixPath

from .source_viewer import SourceError, _read_regular_file
from .python_entrypoint_syntax import PythonUnit, Receiver, bind_registration_parameters, literal, qualified

MAX_FILES, MAX_BYTES, MAX_AST_NODES, MAX_ENTRIES = 1200, 16 * 1024 * 1024, 800_000, 4000
HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace', 'api_route'}
MCP_METHODS = {'tool', 'resource', 'prompt'}


def _units(root, by_file, coverage, source_roots):
    """Read graph-owned regular UTF-8 files, never imports, symlinks or build commands."""
    units, size, ast_count = [], 0, 0
    for index, filename in enumerate(sorted(by_file)):
        if index >= MAX_FILES or size >= MAX_BYTES:
            coverage['limited'] = True
            break
        try:
            payload = _read_regular_file(root, Path(filename))
            size += len(payload)
            if size > MAX_BYTES:
                coverage['limited'] = True
                break
            text = payload.decode('utf-8-sig')
            tree = ast.parse(text)
            count = sum(1 for _ in ast.walk(tree))
            ast_count += count
            if ast_count > MAX_AST_NODES:
                coverage['limited'] = True
                break
            units.append(PythonUnit(filename, text, sha256(payload).hexdigest(), tree, source_roots))
            coverage['readFiles'] += 1
        except (OSError, SourceError, UnicodeError, SyntaxError, RecursionError):
            coverage['unavailableFiles'] += 1
    return units


def _handler(scope, nodes):
    """Bind one exact declaration occurrence; ambiguous source identities remain unresolved."""
    declaration = scope['declaration']
    start = min([declaration.lineno, *[item.lineno for item in declaration.decorator_list]])
    matches = []
    for node in nodes:
        name = str(node.get('label', '')).split('(')[0].split('.')[-1]
        if name == declaration.name and type(node.get('line')) is int and start <= node['line'] <= declaration.lineno:
            matches.append(node)
    return matches[0] if len(matches) == 1 else None


def _http_declarations(call, name, receiver, declaration):
    """Read local FastAPI paths; router mounting and computed method lists stay unresolved."""
    if name not in HTTP_METHODS or call is None:
        return
    keywords = {item.arg: item.value for item in call.keywords if item.arg}
    route = literal(call.args[0]) if call.args else literal(keywords.get('path'))
    methods = [name.upper()]
    if name == 'api_route':
        expression = keywords.get('methods')
        methods = [literal(item) for item in expression.elts] if isinstance(expression, (ast.List, ast.Tuple)) else []
        allowed = {method.upper() for method in HTTP_METHODS - {'api_route'}}
        if not methods or not all(value and value.upper() in allowed for value in methods):
            methods = [None]
    for method in methods:
        full_route = receiver.prefix + route if route is not None and receiver.prefix is not None else None
        yield {'kind': 'http', 'route': full_route, 'httpMethod': method.upper() if method else None,
               'routeStatus': 'router-local-path' if receiver.router else 'declared-path',
               'label': full_route if full_route is not None else declaration.name,
               'unresolved': route is None or receiver.prefix is None or method is None}


def _mcp_declarations(call, name, receiver, declaration):
    """Separate MCP tool/resource/prompt registrations; a dynamic name is not a known operation."""
    if name not in MCP_METHODS or call is None:
        return
    keywords = {item.arg: item.value for item in call.keywords if item.arg}
    name_argument = keywords.get('name')
    if name != 'resource' and name_argument is None and call.args:
        name_argument = call.args[0]
    explicit = literal(name_argument)
    default_name = name_argument is None or (isinstance(name_argument, ast.Constant) and name_argument.value is None)
    dynamic_name = not default_name and explicit is None
    operation = explicit or declaration.name
    uri = literal(call.args[0]) if name == 'resource' and call.args else literal(keywords.get('uri'))
    yield {'kind': 'mcp', 'mcpType': name, 'operationName': None if dynamic_name else operation,
           'uri': uri, 'label': operation, 'unresolved': dynamic_name or (name == 'resource' and uri is None)}


STRATEGIES = {'python-fastapi-v1': _http_declarations, 'python-fastmcp-v1': _mcp_declarations}


def _registrations(scope, providers, coverage):
    """Dispatch normalized library declarations only after resolving their lexical receiver."""
    declaration = scope['declaration']
    environment = scope['parent']['env'] if scope['parent'] else scope['env']
    for decorator in declaration.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        function = call.func if call else decorator
        if not isinstance(function, ast.Attribute):
            continue
        receiver = qualified(function.value, environment)
        if not isinstance(receiver, Receiver) or receiver.strategy not in providers:
            if function.attr in HTTP_METHODS | MCP_METHODS:
                coverage['unresolvedReceivers'] += 1
            continue
        strategy = STRATEGIES[receiver.strategy]
        for registration in strategy(call, function.attr, receiver, declaration):
            yield decorator, providers[receiver.strategy], registration


def capture_python_entrypoints(graph, root, rules):
    """Attach pinned library facts to existing handlers without changing raw graph identities.

    Python factories/registration helpers may never run. Entries describe lexical
    registrations; router mounting, dependency injection and transport stay unknown.
    """
    providers = {item['strategy']: item for item in rules.get('entrypointProviders', []) if item.get('language') == 'python'}
    if not providers:
        if graph.get('metadata', {}).get('entrypointDiscovery') or any(node.get('entrypointFacts') for node in graph.get('nodes', [])):
            metadata = dict(graph.get('metadata') or {})
            metadata.pop('entrypointDiscovery', None)
            nodes = [{key: value for key, value in node.items() if key != 'entrypointFacts'} for node in graph.get('nodes', [])]
            return {**graph, 'nodes': nodes, 'metadata': metadata}
        return graph
    nodes = [deepcopy(node) for node in graph.get('nodes', [])]
    by_file = defaultdict(list)
    for node in nodes:
        node.pop('entrypointFacts', None)
        filename = str(node.get('file') or node.get('source_file') or '').replace('\\', '/')
        path = PurePosixPath(filename)
        if node.get('kind') != 'external' and path.suffix == '.py' and not path.is_absolute() and '..' not in path.parts and ':' not in filename:
            by_file[filename].append(node)
    coverage = {'candidateFiles': len(by_file), 'readFiles': 0, 'unavailableFiles': 0,
                'unmappedHandlers': 0, 'unresolvedReceivers': 0, 'unresolvedRegistrations': 0,
                'limited': False, 'maxEntries': MAX_ENTRIES}
    source_roots = [marker for rule in rules.get('projectRules', []) if rule.get('language') == 'python'
                    for marker in rule.get('sourceRootMarkers', [])]
    units = _units(Path(root), by_file, coverage, source_roots)
    bind_registration_parameters(units)
    entries = []
    for unit in units:
        for scope in unit.scopes:
            if not isinstance(scope['declaration'], (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator, provider, registration in _registrations(scope, providers, coverage):
                if len(entries) >= MAX_ENTRIES:
                    coverage['limited'] = True
                    break
                handler = _handler(scope, by_file[unit.filename])
                identity = f"{unit.filename}:{scope['name']}:{decorator.lineno}:{registration['kind']}:{registration.get('httpMethod')}"
                entry = {**registration, 'id': 'python-entry-' + sha256(identity.encode()).hexdigest()[:20],
                    'handlerNodeIds': [handler['id']] if handler else [], 'file': unit.filename,
                    'line': decorator.lineno, 'handlerName': scope['name'], 'language': 'python',
                    'framework': provider['id'], 'modId': provider['modId'],
                    'modFingerprint': provider['modFingerprint'], 'sourceKind': 'snapshot-captured-source',
                    'sourceHash': unit.byte_hash, 'runtimeStatus': 'unknown', 'confidence': 'source-declaration',
                    'origin': 'Внешний клиент; выполнение регистрации, окружение и транспорт не установлены.',
                    'codeProjectId': handler.get('codeProjectId', '__unassigned__') if handler else '__unassigned__',
                    'evidence': [{'file': unit.filename, 'line': decorator.lineno,
                        'declaration': (ast.get_source_segment(unit.text, decorator) or '')[:600],
                        'interpretation': 'Lexical registration; execution, mounting and transport are not established.'}]}
                entry['unresolved'] = ['Имя, маршрут или HTTP-метод вычисляется.'] if registration['unresolved'] else []
                if registration.get('routeStatus') == 'router-local-path':
                    entry['unresolved'].append('Показан локальный путь APIRouter; префикс include_router не установлен.')
                if entry['unresolved']:
                    coverage['unresolvedRegistrations'] += 1
                if handler:
                    handler.setdefault('entrypointFacts', []).append(deepcopy(entry))
                else:
                    coverage['unmappedHandlers'] += 1
                    entry['unresolved'].append('Обработчик не сопоставлен однозначно с узлом карты.')
                entries.append(entry)
    metadata = deepcopy(graph.get('metadata') or {})
    metadata['entrypointDiscovery'] = {'version': 'python-entrypoints-v1', 'sourceKind': 'snapshot-captured-source',
        'status': 'partial' if coverage['limited'] or any(coverage[key] for key in
            ('unavailableFiles', 'unmappedHandlers', 'unresolvedReceivers', 'unresolvedRegistrations')) else 'complete',
        'coverage': coverage, 'entries': entries}
    return {**graph, 'nodes': nodes, 'metadata': metadata}
