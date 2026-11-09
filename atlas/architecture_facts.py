"""Захват ограниченного набора файлов и статических зависимостей без исполнения кода."""
import ast
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from .architecture_rules import ANALYZER, Rules
from .project_scope_context import ProjectScopeContext
from .source_viewer import read_node_source, SourceError
from .system_architecture import _manifest_references

MAX_FILES = 20000
MAX_BYTES = 16 * 1024 * 1024
MAX_EDGES = 100000
EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'build', 'dist', '.agents', '.cursor', '.claude'}


def fingerprint(value) -> str:
    """Хеширует канонический JSON; время чтения и порядок словарей не участвуют."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def inventory(root: Path, exclusions=()) -> tuple[list[str], list[str]]:
    """Перечисляет обычные пути без обхода symlink; лимит и ошибки оставляет явными."""
    files, reasons = [], []
    excluded = set(exclusions)
    def failed(_):
        """Ошибка каталога делает общий inventory неполным."""
        reasons.append('inventory-unreadable')
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        # Пропущенная ссылка может скрывать область: отсутствие зависимости не доказано.
        for name in dirs:
            relative = (Path(directory) / name).relative_to(root).as_posix()
            if name not in EXCLUDED and relative not in excluded and (Path(directory) / name).is_symlink():
                reasons.append('inventory-symlink')
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED and (Path(directory) / name).relative_to(root).as_posix() not in excluded
                         and not (Path(directory) / name).is_symlink())
        for name in sorted(names):
            relative = (Path(directory) / name).relative_to(root).as_posix()
            if relative in excluded:
                continue
            files.append(relative)
            if len(files) >= MAX_FILES:
                return sorted(files), sorted(set(reasons + ['inventory-limit']))
    return sorted(files), sorted(set(reasons))


def select_layers(config: Rules, files: list[str], scope: ProjectScopeContext) -> tuple[dict, dict]:
    """Присваивает области файлам; неизвестные проекты и пересечения не угадываются."""
    memberships = {layer.id: set() for layer in config.layers}
    reasons = {layer.id: [] for layer in config.layers}
    projects = {project['id']: project for project in scope.projects}
    owners_by_file = {}
    for node in scope.nodes:
        owners_by_file.setdefault(node.get('file') or node.get('source_file'), set()).add(node.get('codeProjectId'))
    for layer in config.layers:
        if layer.projects:
            # Проектные области опираются на снимок, не на полный новый discovery.
            reasons[layer.id].append('snapshot-project-inventory-not-proven')
        for project_id in layer.projects:
            if project_id not in projects:
                reasons[layer.id].append('unknown-project:' + project_id)
        for file in files:
            if any(fnmatchcase(file, pattern) for pattern in layer.paths):
                memberships[layer.id].add(file)
            elif layer.projects:
                owners = owners_by_file.get(file, set())
                if len(owners) == 1 and owners & set(layer.projects):
                    memberships[layer.id].add(file)
                elif owners & set(layer.projects):
                    reasons[layer.id].append('ambiguous-project-ownership')
        if not memberships[layer.id]:
            reasons[layer.id].append('empty-selector')
    by_file = {}
    for layer, selected in memberships.items():
        for file in selected:
            by_file.setdefault(file, []).append(layer)
    for file, layers in by_file.items():
        if len(layers) > 1:
            for layer in layers:
                reasons[layer].append('overlapping-selector:' + file)
    return memberships, reasons


def _module_name(file: str, python_root: str = '.') -> str | None:
    """Переводит repository-relative .py в лексическое имя модуля (без sys.path)."""
    try:
        relative = PurePosixPath(file).relative_to(python_root)
    except ValueError:
        return None
    parts = list(relative.with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join(parts)


def _python_imports(file: str, text: str, modules: dict, python_root: str) -> list[tuple[str, int]]:
    """Извлекает все AST import, включая вложенные; динамический runtime вне контракта."""
    tree = ast.parse(text, filename=file)
    name = _module_name(file, python_root)
    if name is None:
        raise ValueError('source outside configured Python root')
    package = name.split('.') if name else []
    if PurePosixPath(file).name != '__init__.py':
        package.pop()
    result = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > len(package):
                raise ValueError('relative import escapes source root')
            prefix = package[:len(package) - node.level + 1] if node.level else []
            base = '.'.join(prefix + ([node.module] if node.module else []))
            names = [base]
            names.extend((base + '.' if base else '') + alias.name for alias in node.names if alias.name != '*')
        for name in names:
            # Лексический import включает пакеты-предки; это не утверждение runtime binding.
            pieces = name.split('.')
            for end in range(1, len(pieces) + 1):
                target = modules.get('.'.join(pieces[:end]))
                if target and target != file:
                    result.add((target, node.lineno))
    return sorted(result)


def capture(root: Path, graph: dict, snapshot_id: str, config: Rules) -> dict:
    """Фиксирует байты области и проверяет их повторно; stale-факты не подтверждают нарушение."""
    root = root.resolve()
    files, inventory_reasons = inventory(root, config.exclude)
    scope = ProjectScopeContext(root, graph)
    layers, layer_reasons = select_layers(config, files, scope)
    selected = sorted(set().union(*layers.values()))
    modules, collisions = {}, set()
    for file in files:
        if not file.endswith('.py'):
            continue
        name = _module_name(file, config.pythonRoot or '.')
        if name is None:
            continue
        if name in modules:
            collisions.add(name)
        modules[name] = file
    # Коллизия package/module блокирует также все дочерние qualified names.
    for name in list(modules):
        parts = name.split('.')
        if any('.'.join(parts[:end]) in collisions for end in range(1, len(parts) + 1)):
            modules.pop(name)
    sources, errors, total = {}, {}, 0
    for file in selected:
        if total > MAX_BYTES:
            errors[file] = 'source-byte-limit'
            continue
        try:
            source = read_node_source(root, {'file': file}, snapshot_id, include_syntax=False)
            total += len(source['text'].encode())
            if total > MAX_BYTES:
                errors[file] = 'source-byte-limit'
                continue
            sources[file] = source
        except SourceError:
            errors[file] = 'source-unreadable'
    edges, completeness = [], {}
    needed = {relation for rule in config.rules for relation in rule.relations}
    for file, source in sources.items():
        completeness[file] = {}
        for relation in sorted(needed):
            facts, reasons = [], []
            try:
                if relation == 'python-import' and file.endswith('.py'):
                    facts = _python_imports(file, source['text'], modules, config.pythonRoot)
                    if collisions:
                        reasons.append('ambiguous-python-modules')
                elif relation == 'project-reference' and Path(file).suffix.lower() in {'.csproj', '.fsproj', '.vbproj'}:
                    for reference in _manifest_references(source['text']):
                        if reference['kind'] != 'ProjectReference':
                            continue
                        include = reference['include'].replace('\\', '/')
                        if '$' in include or '@(' in include or not include:
                            reasons.append('unevaluated-project-reference')
                            continue
                        target = os.path.normpath(str(PurePosixPath(file).parent / include))
                        if target.startswith('../') or target.startswith('/'):
                            reasons.append('external-project-reference')
                        else:
                            facts.append((target, reference['line']))
                else:
                    reasons.append('unsupported-file-or-relation')
            except (SyntaxError, ValueError, RecursionError) as error:
                reasons.append('source-parse-error:' + type(error).__name__)
            completeness[file][relation] = sorted(set(reasons))
            for target, line in facts:
                if len(edges) >= MAX_EDGES:
                    completeness[file][relation].append('edge-limit')
                    break
                edges.append({'source': file, 'target': target, 'relation': relation,
                              'confidence': 'declared', 'file': file, 'line': line,
                              'sourceHash': source['sourceByteHash'], 'sourceKind': 'captured-working-tree'})
    # Raw code edges не считаются полнотой. Для доказательства нужны byte hashes
    # обоих методов; legacy graph confidence без этих фактов остаётся неизвестным.
    if 'code-dependency' in needed:
        accepted = {'calls', 'references', 'imports', 'inherits', 'implements', 'dispatches_to'}
        nodes = {node['id']: node for node in graph.get('nodes', [])}
        for edge in graph.get('edges', [])[:MAX_EDGES]:
            if len(edges) >= MAX_EDGES:
                inventory_reasons.append('edge-limit')
                break
            if edge.get('relation') not in accepted or edge.get('confidence') not in {'EXTRACTED', 'resolved'}:
                continue
            left, right = nodes.get(edge['source'], {}), nodes.get(edge['target'], {})
            source, target = left.get('file'), right.get('file')
            captured = left.get('sourceByteHash')
            target_hash = right.get('sourceByteHash')
            target_current = sources.get(target, {}).get('sourceByteHash')
            if source in sources and target_current and captured == sources[source]['sourceByteHash'] and target_hash == target_current:
                edges.append({'source': source, 'target': target, 'relation': 'code-dependency',
                              'confidence': 'resolved', 'file': source, 'line': left.get('line', 1),
                              'sourceHash': captured, 'targetHash': target_hash, 'sourceKind': 'snapshot-validated-source',
                              'nodeIds': [edge['source'], edge['target']]})
    stale = set()
    for file, source in sources.items():
        try:
            current = read_node_source(root, {'file': file}, snapshot_id, include_syntax=False)
            if current['sourceByteHash'] != source['sourceByteHash']:
                stale.add(file)
        except SourceError:
            stale.add(file)
    after_files, after_reasons = inventory(root, config.exclude)
    if files != after_files:
        inventory_reasons.append('inventory-changed-during-capture')
    inventory_reasons.extend(after_reasons)
    edges = [edge for edge in edges if edge['source'] not in stale and edge['target'] not in stale]
    for file in stale:
        errors[file] = 'source-changed-during-capture'
    hashes = {file: source['sourceByteHash'] for file, source in sources.items()}
    return {'snapshotId': snapshot_id, 'analyzerVersion': ANALYZER,
            'sourceKind': 'captured-working-tree+validated-snapshot',
            'sourceFingerprint': fingerprint({'files': files, 'hashes': hashes, 'errors': errors,
                                              'graph': graph if 'code-dependency' in needed else None}),
            'sourceHashes': hashes, 'layers': {key: sorted(value) for key, value in layers.items()},
            'layerReasons': layer_reasons, 'inventoryReasons': sorted(set(inventory_reasons)),
            'errors': errors, 'completeness': completeness,
            'edges': sorted(edges, key=lambda item: (item['source'], item['target'], item['relation'], item['line'])),
            'limits': {'files': MAX_FILES, 'bytes': MAX_BYTES, 'edges': MAX_EDGES}}
