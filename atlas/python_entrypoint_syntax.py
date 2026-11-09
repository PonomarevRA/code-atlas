"""Static Python receiver bindings for library decorators; analyzed modules are never imported."""
import ast
from dataclasses import dataclass
from pathlib import PurePosixPath

FACTORIES = {
    'fastapi.FastAPI': ('python-fastapi-v1', False),
    'fastapi.applications.FastAPI': ('python-fastapi-v1', False),
    'fastapi.APIRouter': ('python-fastapi-v1', True),
    'fastapi.routing.APIRouter': ('python-fastapi-v1', True),
    'mcp.server.fastmcp.FastMCP': ('python-fastmcp-v1', False),
    'mcp.server.fastmcp.server.FastMCP': ('python-fastmcp-v1', False),
    'fastmcp.FastMCP': ('python-fastmcp-v1', False),
}


@dataclass(frozen=True)
class Receiver:
    """A lexical framework instance, not a live app, configured transport or mounted router."""
    strategy: str
    router: bool = False
    prefix: str | None = ''


def qualified(expression, environment):
    """Resolve imported aliases and attributes without guessing unknown object types."""
    if isinstance(expression, ast.Name):
        return environment.get(expression.id)
    if isinstance(expression, ast.Attribute):
        owner = qualified(expression.value, environment)
        if isinstance(owner, str):
            return owner + '.' + expression.attr
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return environment.get(expression.value)
    return None


def literal(expression):
    """Only a bounded string literal is a known route/name; expressions stay opaque."""
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str) and len(expression.value) <= 500:
        return expression.value
    return None


def local_nodes(body):
    """Walk one lexical scope, keeping nested function/class bodies separate."""
    pending = list(reversed(body))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _imports(nodes, module, is_package):
    """Resolve absolute and relative imports to names only; no source module executes."""
    bindings = {}
    package = module.split('.') if is_package else module.split('.')[:-1]
    for node in nodes:
        if isinstance(node, ast.Import):
            for item in node.names:
                bindings[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom):
            prefix = package[:len(package) - node.level + 1] if node.level else []
            owner = '.'.join([*prefix, *([node.module] if node.module else [])])
            for item in node.names:
                if item.name != '*':
                    bindings[item.asname or item.name] = owner + '.' + item.name
    return bindings


def _receiver(expression, environment):
    """Recognize explicit library construction or a previously resolved lexical instance."""
    if not isinstance(expression, ast.Call):
        value = qualified(expression, environment)
        return value if isinstance(value, Receiver) else None
    factory = qualified(expression.func, environment)
    if not isinstance(factory, str) or factory not in FACTORIES:
        return None
    strategy, router = FACTORIES[factory]
    prefix = ''
    for keyword in expression.keywords:
        if keyword.arg == 'prefix':
            prefix = literal(keyword.value)
    return Receiver(strategy, router, prefix)


class PythonUnit:
    """One safely read file with bounded AST and independently tracked lexical scopes."""
    def __init__(self, filename, text, byte_hash, tree, source_roots=()):
        """Use an already parsed, budget-checked tree; retain source hash for captured evidence."""
        self.filename, self.text, self.byte_hash = filename, text, byte_hash
        path = PurePosixPath(filename)
        parts = path.with_suffix('').parts
        start = 0
        for marker in source_roots:
            expected = PurePosixPath(marker).parts
            for index in range(len(parts) - len(expected)):
                if parts[index:index + len(expected)] == expected:
                    start = max(start, index + len(expected))
        self.module = '.'.join(parts[start:])
        is_package = path.name == '__init__.py'
        if is_package:
            self.module = self.module.removesuffix('.__init__')
        self.scopes = []
        self._scope(tree, '', {}, is_package, None)

    def _scope(self, declaration, name, inherited, is_package, parent):
        """Resolve local assignments and parameter annotations; shadowing clears inherited values."""
        nodes = list(local_nodes(declaration.body))
        environment = dict(inherited)
        environment.update(_imports(nodes, self.module, is_package))
        local_names = set(_imports(nodes, self.module, is_package))
        assigned = set()
        parameters = []
        if isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parameters = [*declaration.args.posonlyargs, *declaration.args.args, *declaration.args.kwonlyargs]
            for parameter in parameters:
                annotation = qualified(parameter.annotation, environment)
                environment[parameter.arg] = Receiver(*FACTORIES[annotation]) if isinstance(annotation, str) and annotation in FACTORIES else None
                local_names.add(parameter.arg)
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                suffix = '.'.join(filter(None, [name, node.name]))
                environment[node.name] = self.module + '.' + suffix
                local_names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = _receiver(node.value, environment)
                for target in targets:
                    if isinstance(target, ast.Name):
                        environment[target.id] = value
                        assigned.add(target.id)
                        local_names.add(target.id)
        scope = {'unit': self, 'declaration': declaration, 'name': name,
                 'key': '.'.join(filter(None, [self.module, name])), 'env': environment,
                 'parameters': parameters, 'nodes': nodes, 'parent': parent,
                 'locals': local_names, 'assigned': assigned}
        self.scopes.append(scope)
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._scope(node, '.'.join(filter(None, [name, node.name])), environment, is_package, scope)


def bind_registration_parameters(units):
    """Pass known app instances into exact imported/local registration functions, up to three hops.

    Conflicting framework arguments stay unknown. This is lexical call evidence,
    not proof that a factory or registration function runs in a deployment.
    """
    scopes = [scope for unit in units for scope in unit.scopes]
    targets = {}
    for scope in scopes:
        targets[scope['key']] = None if scope['key'] in targets else scope
    for _ in range(3):
        claims = {}
        for scope in scopes:
            if scope['parent']:
                for name, value in scope['parent']['env'].items():
                    if name not in scope['locals']:
                        scope['env'][name] = value
            for call in scope['nodes']:
                if not isinstance(call, ast.Call):
                    continue
                key = qualified(call.func, scope['env'])
                target = targets.get(key) if isinstance(key, str) else None
                if target is None or not isinstance(target['declaration'], (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                positional = [parameter for parameter in target['parameters'] if parameter not in getattr(target['declaration'].args, 'kwonlyargs', [])]
                arguments = [(parameter.arg, argument) for parameter, argument in zip(positional, call.args)]
                arguments.extend((keyword.arg, keyword.value) for keyword in call.keywords if keyword.arg)
                for name, argument in arguments:
                    if name not in {parameter.arg for parameter in target['parameters']}:
                        continue
                    if name in target['assigned']:
                        continue
                    value = qualified(argument, scope['env'])
                    if isinstance(value, Receiver):
                        claims.setdefault((key, name), set()).add(value)
        changed = False
        for (key, name), values in claims.items():
            value = next(iter(values)) if len(values) == 1 else None
            if targets[key]['env'].get(name) != value:
                targets[key]['env'][name] = value
                changed = True
        if not changed:
            break
