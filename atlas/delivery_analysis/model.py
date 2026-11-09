"""Ограниченная immutable-модель синтетической задачи вариативной поставки."""

from dataclasses import dataclass
import hashlib
import json

import networkx as nx

VERSION = 'atlas-delivery-problem/v1'
MAX_INPUT_BYTES = 128 * 1024
MAX_FILES = 32
MAX_REQUIREMENTS = 64
MAX_CONFIGURATIONS = 16
MAX_MODULES = 8


class DeliveryAnalysisError(ValueError):
    """Явно некорректный вход или превышенный бюджет offline-прототипа."""


def canonical_hash(value):
    """Связывает результат с нормализованными конечными JSON-значениями."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _identifier(value):
    """Принимает логический ID, а не путь для чтения содержимого файла."""
    if not isinstance(value, str) or not 0 < len(value) <= 128 or value.strip() != value or not value.strip() or any(ord(char) < 32 for char in value):
        raise DeliveryAnalysisError('IDs must be bounded nonempty strings without control characters')
    return value


def _members(values, allowed, *, empty=False):
    """Проверяет уникальный набор ссылок на уже объявленные IDs."""
    if not isinstance(values, list) or len(values) > len(allowed) or (not empty and not values):
        raise DeliveryAnalysisError('Expected a bounded nonempty ID array')
    result = tuple(sorted(_identifier(value) for value in values))
    if len(set(result)) != len(result) or any(value not in allowed for value in result):
        raise DeliveryAnalysisError('Unknown or duplicate referenced ID')
    return result


@dataclass(frozen=True)
class DeliveryProblem:
    """Полностью проверенные tuples исключают изменение данных между оценками."""

    files: tuple[str, ...]
    dependencies: tuple[tuple[str, str], ...]
    requirements: tuple[tuple[str, tuple[str, ...]], ...]
    configurations: tuple[tuple[str, tuple[str, ...]], ...]
    module_limit: int
    problem_hash: str

    def as_dict(self):
        """Возвращает свежую JSON-проекцию; вложенные структуры не меняют модель."""
        return {'schemaVersion': VERSION, 'source': 'declared-synthetic',
                'files': list(self.files), 'dependencies': [list(edge) for edge in self.dependencies],
                'requirements': {key: list(files) for key, files in self.requirements},
                'configurations': {key: list(required) for key, required in self.configurations},
                'moduleLimit': self.module_limit, 'direction': 'dependent-to-prerequisite'}


def prepare_problem(data):
    """
    Проверяет синтетический вход без чтения файлов проекта и без исполнения кода.

    dependencies=[A,B] означает A depends on B. Requirements требуют ВСЕХ
    перечисленных файлов, а moduleLimit задаёт не более k непустых модулей.
    Весовая Q-матрица статьи заменена эквивалентным all-files-required набором.
    """
    if isinstance(data, DeliveryProblem):
        return data
    expected = {'schemaVersion', 'source', 'files', 'dependencies', 'requirements', 'configurations', 'moduleLimit'}
    if not isinstance(data, dict) or set(data) not in (expected, expected | {'direction'}):
        raise DeliveryAnalysisError('Delivery input fields do not match the versioned schema')
    if data.get('direction', 'dependent-to-prerequisite') != 'dependent-to-prerequisite':
        raise DeliveryAnalysisError('Dependency direction must be dependent-to-prerequisite')
    try:
        raw = json.dumps(data, allow_nan=False, ensure_ascii=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError) as error:
        raise DeliveryAnalysisError('Input must contain finite JSON values') from error
    if len(raw) > MAX_INPUT_BYTES:
        raise DeliveryAnalysisError('Delivery input exceeds 128 KiB')
    if data['schemaVersion'] != VERSION or data['source'] != 'declared-synthetic':
        raise DeliveryAnalysisError('This prototype accepts declared synthetic schema-v1 cases only')
    files = data['files']
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise DeliveryAnalysisError(f'Expected 1..{MAX_FILES} files')
    files = tuple(sorted(_identifier(value) for value in files))
    if len(set(files)) != len(files):
        raise DeliveryAnalysisError('Duplicate file ID')
    edges = data['dependencies']
    if not isinstance(edges, list) or len(edges) > MAX_FILES ** 2:
        raise DeliveryAnalysisError('Dependency edge budget exceeded')
    dependencies = set()
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2 or any(not isinstance(value, str) or value not in files for value in edge):
            raise DeliveryAnalysisError('Dependency must reference two known file IDs')
        if edge[0] == edge[1]:
            raise DeliveryAnalysisError('Self-dependency is not part of this model')
        dependencies.add(tuple(edge))
    requirements = data['requirements']
    if not isinstance(requirements, dict) or not 1 <= len(requirements) <= MAX_REQUIREMENTS:
        raise DeliveryAnalysisError(f'Expected 1..{MAX_REQUIREMENTS} requirement mappings')
    traced = tuple(sorted((_identifier(key), _members(value, files)) for key, value in requirements.items()))
    configurations = data['configurations']
    if not isinstance(configurations, dict) or not 1 <= len(configurations) <= MAX_CONFIGURATIONS:
        raise DeliveryAnalysisError(f'Expected 1..{MAX_CONFIGURATIONS} configurations')
    required_ids = {key for key, _ in traced}
    variants = tuple(sorted((_identifier(key), _members(value, required_ids, empty=True)) for key, value in configurations.items()))
    limit = data['moduleLimit']
    if type(limit) is not int or not 1 <= limit <= MAX_MODULES:
        raise DeliveryAnalysisError(f'moduleLimit must be an integer 1..{MAX_MODULES}')
    normalized = {'schemaVersion': VERSION, 'files': files, 'dependencies': sorted(dependencies), 'requirements': traced,
                  'configurations': variants, 'moduleLimit': limit, 'direction': 'dependent-to-prerequisite'}
    return DeliveryProblem(files, tuple(sorted(dependencies)), traced, variants, limit, canonical_hash(normalized))


def dependency_components(problem):
    """
    Возвращает SCC в dependency-first топологическом порядке с deterministic ties.

    Входные depends-on дуги разворачиваются ТОЛЬКО для порядка prerequisites,
    а не для вычисления closure. Несогласованная конвенция статьи не переносится.
    """
    problem = prepare_problem(problem)
    graph = nx.DiGraph()
    graph.add_nodes_from(problem.files)
    graph.add_edges_from(problem.dependencies)
    groups = sorted(tuple(sorted(group)) for group in nx.strongly_connected_components(graph))
    owners = {file: index for index, group in enumerate(groups) for file in group}
    condensed = nx.DiGraph()
    condensed.add_nodes_from(range(len(groups)))
    for dependent, prerequisite in problem.dependencies:
        source, target = owners[prerequisite], owners[dependent]
        if source != target:
            condensed.add_edge(source, target)
    return tuple(groups[index] for index in nx.lexicographical_topological_sort(condensed, key=lambda index: groups[index]))


def validate_assignment(problem, assignment, *, require_scc=True):
    """Проверяет полный file→module контракт, бюджет k и неделимость SCC."""
    if type(require_scc) is not bool:
        raise DeliveryAnalysisError('require_scc must be a boolean')
    if not isinstance(assignment, dict) or set(assignment) != set(problem.files):
        raise DeliveryAnalysisError('Assignment must cover every file exactly once')
    for label in assignment.values():
        _identifier(label)
    if len(set(assignment.values())) > problem.module_limit:
        raise DeliveryAnalysisError('Assignment exceeds moduleLimit')
    if require_scc:
        for group in dependency_components(problem):
            if len({assignment[file] for file in group}) != 1:
                raise DeliveryAnalysisError('An SCC cannot be split in this analysis mode')
    return {file: assignment[file] for file in problem.files}
