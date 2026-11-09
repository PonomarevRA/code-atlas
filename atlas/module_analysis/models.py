"""Версионированные границы и валидация чистого анализа модулей."""

import hashlib
import json
from pathlib import PurePosixPath

VERSION = 'module-analysis-v1'
MAX_NODES = 10_000
MAX_EDGES = 50_000
MAX_UNITS = 2_000
MAX_SOURCE_NODES = 100_000
MAX_SOURCE_EDGES = 500_000
DEPENDENCIES = frozenset({'calls', 'imports', 'references', 'inherits', 'implements', 'depends_on'})
CONTAINMENT = frozenset({'contains', 'defines', 'method', 'case_of'})
CLASS_KINDS = frozenset({'class', 'interface', 'struct', 'record'})


class ModuleAnalysisError(ValueError):
    """Некорректные или превышающие бюджет данные; запись графа не выполняется."""

    def __init__(self, message, *, code='module_analysis_invalid', status=400):
        """Сохраняет стабильный код и HTTP-подсказку без зависимости от FastAPI."""
        super().__init__(message)
        self.code = code
        self.status = status


def digest(value):
    """
    Возвращает SHA-256 канонических JSON-фактов без зависимости от форматирования.
    """
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ModuleAnalysisError('Graph facts must be finite JSON values') from error
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def project_path(value, *, folder=False):
    """
    Проверяет относительный путь проекта без обращения к файловой системе.

    Абсолютные пути, обход каталогов и неоднозначный корень отклоняются.
    """
    if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value:
        raise ModuleAnalysisError('Expected a nonempty project-relative path')
    text = value.replace('\\', '/')
    path = PurePosixPath(text)
    if path.is_absolute() or ':' in text or '..' in path.parts:
        raise ModuleAnalysisError('Path must stay inside the project')
    if not folder and path.as_posix() == '.':
        raise ModuleAnalysisError('A file path cannot be the project root')
    return path.as_posix()


def validate_graph(graph, *, scoped=False):
    """
    Проверяет контейнеры, идентификаторы и бюджет до построения вспомогательных графов.
    """
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('edges'), list):
        raise ModuleAnalysisError('Graph requires node and edge arrays')
    if graph.get('metadata') is not None and not isinstance(graph['metadata'], dict):
        raise ModuleAnalysisError('metadata must be an object')
    max_nodes, max_edges = (MAX_SOURCE_NODES, MAX_SOURCE_EDGES) if scoped else (MAX_NODES, MAX_EDGES)
    if len(graph['nodes']) > max_nodes or len(graph['edges']) > max_edges:
        raise ModuleAnalysisError(f'Graph budget exceeded: {max_nodes} nodes / {max_edges} edges; select a smaller folder scope',
                                  code='module_analysis_budget', status=413)
    identifiers = set()
    for node in graph['nodes']:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not 0 < len(node['id']) <= 1024:
            raise ModuleAnalysisError('Node requires a bounded string ID')
        if node['id'] in identifiers:
            raise ModuleAnalysisError('Duplicate raw node ID')
        identifiers.add(node['id'])
        for key in ('declarationFacts', 'memberFacts', 'complexity'):
            if node.get(key) is not None and not isinstance(node[key], dict):
                raise ModuleAnalysisError(f'{key} must be an object')
    for edge in graph['edges']:
        if not isinstance(edge, dict) or any(not isinstance(edge.get(k), str) for k in ('source', 'target')):
            raise ModuleAnalysisError('Edge requires string source and target')
        if edge.get('evidence') is not None and not isinstance(edge['evidence'], dict):
            raise ModuleAnalysisError('Edge evidence must be an object')
    return {node['id']: node for node in graph['nodes']}


def validate_partition(module_graph, partition=None):
    """
    Возвращает непересекающееся полное разбиение; пропуски не заполняются догадками.
    """
    identifiers = {unit['id'] for unit in module_graph['units']}
    if partition is None:
        return [[identifier] for identifier in sorted(identifiers)]
    if not isinstance(partition, list) or len(partition) > MAX_UNITS:
        raise ModuleAnalysisError('Partition requires a bounded array of groups')
    seen, groups = set(), []
    for group in partition:
        if not isinstance(group, (list, tuple, set)) or not group:
            raise ModuleAnalysisError('Partition groups must be nonempty arrays')
        members = set()
        for member in group:
            if not isinstance(member, str) or member not in identifiers or member in seen or member in members:
                raise ModuleAnalysisError('Partition contains unknown or repeated unit IDs')
            members.add(member)
        seen.update(members)
        groups.append(sorted(members))
    if seen != identifiers:
        raise ModuleAnalysisError('Partition must cover every unit exactly once')
    return sorted(groups)
