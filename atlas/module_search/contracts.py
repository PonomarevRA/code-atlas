"""
Снимок входа и жёсткие ограничения поиска; файловая система не используется.
"""

from copy import deepcopy
from dataclasses import dataclass

from atlas.module_analysis.models import VERSION, ModuleAnalysisError, digest, validate_partition

MAX_UNITS = 128
MAX_EDGES = 10_000
MAX_EVALUATIONS = 256
MAX_PROPOSALS = 16


class ModuleSearchError(ValueError):
    """
    Невалидный или превышающий бюджет вход с пригодным для адаптера кодом.
    """

    def __init__(self, message, *, code='module_search_invalid', status=400):
        self.code, self.status = code, status
        super().__init__(message)


class Cancelled(Exception):
    """
    Внутреннее прекращение вычисления без выдачи незавершённой оценки.
    """


def check_cancel(cancelled):
    """
    Проверяет cooperative callback; исключения самого callback не скрываются.
    """
    if cancelled is not None and cancelled():
        raise Cancelled()


def bounded_int(value, name, minimum, maximum):
    """
    Проверяет целый параметр, исключая bool и неограниченные значения.
    """
    if type(value) is not int or not minimum <= value <= maximum:
        raise ModuleSearchError(f'{name} must be an integer in [{minimum}, {maximum}]')
    return value


def canonical_labels(labels):
    """
    Нормализует номера групп по первому участнику, сохраняя только membership.
    """
    renaming, result = {}, []
    for label in labels:
        if label not in renaming:
            renaming[label] = len(renaming)
        result.append(renaming[label])
    return tuple(result)


@dataclass(frozen=True)
class Problem:
    """
    Владеет копией identity и неизменяемыми фактами одного ограниченного поиска.
    """
    ids: tuple
    edges: tuple
    current: tuple
    blocks: tuple
    apart: tuple
    limits: tuple
    identity: dict
    constraints: dict

    def partition(self, labels):
        """
        Возвращает полные группы исходных unit IDs без переноса или переименования.
        """
        groups = {}
        for unit, label in zip(self.ids, labels):
            groups.setdefault(label, []).append(unit)
        return sorted(groups.values())

    def labels(self, partition):
        """
        Проверяет полное покрытие тем же существующим partition-контрактом Atlas.
        """
        try:
            groups = validate_partition({'units': [{'id': unit} for unit in self.ids]}, partition)
        except ModuleAnalysisError as error:
            raise ModuleSearchError(str(error)) from error
        assignment = {unit: index for index, group in enumerate(groups) for unit in group}
        return canonical_labels(assignment[unit] for unit in self.ids)

    def feasible(self, labels):
        """
        Проверяет размеры и together/apart; никогда не исправляет ограничения молча.
        """
        sizes = {}
        for label in labels:
            sizes[label] = sizes.get(label, 0) + 1
        low_size, high_size, low_groups, high_groups = self.limits
        if not low_groups <= len(sizes) <= high_groups:
            return False
        if any(not low_size <= size <= high_size for size in sizes.values()):
            return False
        for block in self.blocks:
            if any(labels[index] != labels[block[0]] for index in block):
                return False
        return all(labels[left] != labels[right] for left, right in self.apart)


def _constraints(ids, supplied):
    """
    Строит неделимые компоненты must-link и проверяет противоречия cannot-link.
    """
    if supplied is None:
        supplied = {}
    names = {'mustLink', 'cannotLink', 'minGroupUnits', 'maxGroupUnits', 'minGroups', 'maxGroups'}
    if not isinstance(supplied, dict) or set(supplied) - names:
        raise ModuleSearchError('Unknown constraints; use explicit together/apart/size/group limits')
    n = len(ids)
    position = {unit: index for index, unit in enumerate(ids)}
    parents = list(range(n))

    def root(index):
        """
        Находит представителя небольшой компоненты без рекурсии.
        """
        while parents[index] != index:
            index = parents[index]
        return index

    pairs = {}
    for name in ('mustLink', 'cannotLink'):
        values = supplied.get(name, [])
        if not isinstance(values, list) or len(values) > MAX_UNITS * MAX_UNITS:
            raise ModuleSearchError(f'{name} requires bounded pairs of known unit IDs')
        normalized = set()
        for pair in values:
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                    or any(not isinstance(unit, str) or unit not in position for unit in pair)):
                raise ModuleSearchError(f'{name} contains an unknown or malformed unit pair')
            left, right = sorted((position[pair[0]], position[pair[1]]))
            normalized.add((left, right))
            if name == 'mustLink':
                parents[root(right)] = root(left)
        pairs[name] = tuple(sorted(normalized))
    components = {}
    for index in range(n):
        components.setdefault(root(index), []).append(index)
    blocks = tuple(sorted(tuple(members) for members in components.values()))
    if any(root(left) == root(right) for left, right in pairs['cannotLink']):
        raise ModuleSearchError('Together/apart constraints contradict each other')
    low_size = bounded_int(supplied.get('minGroupUnits', 1), 'minGroupUnits', 1, MAX_UNITS)
    high_size = bounded_int(supplied.get('maxGroupUnits', max(n, 1)), 'maxGroupUnits', low_size, MAX_UNITS)
    low_groups = bounded_int(supplied.get('minGroups', int(n > 0)), 'minGroups', int(n > 0), n)
    high_groups = bounded_int(supplied.get('maxGroups', n), 'maxGroups', low_groups, n)
    normalized = {name: [[ids[a], ids[b]] for a, b in values] for name, values in pairs.items()}
    normalized.update(minGroupUnits=low_size, maxGroupUnits=high_size, minGroups=low_groups, maxGroups=high_groups)
    return blocks, pairs['cannotLink'], (low_size, high_size, low_groups, high_groups), normalized


def prepare(projection, current_partition, constraints=None):
    """
    Копирует bounded projection и проверяет identity до поиска; чужие JSON не парсятся как код.
    """
    if (not isinstance(projection, dict) or projection.get('version') != VERSION
            or projection.get('level') not in {'class', 'folder'}
            or not isinstance(projection.get('units'), list) or not isinstance(projection.get('edges'), list)):
        raise ModuleSearchError('Expected an existing versioned class/folder module projection')
    if len(projection['units']) > MAX_UNITS or len(projection['edges']) > MAX_EDGES:
        raise ModuleSearchError('Search scope exceeds 128 units / 10000 projected edges; select a smaller scope',
                                code='module_search_budget', status=413)
    units = projection['units']
    if any(not isinstance(unit, dict) or not isinstance(unit.get('id'), str)
           or not 0 < len(unit['id']) <= 1024 for unit in units):
        raise ModuleSearchError('Projection contains invalid unit IDs')
    ids = tuple(sorted(unit['id'] for unit in units))
    if len(set(ids)) != len(ids):
        raise ModuleSearchError('Projection contains duplicate unit IDs')
    # A caller-created digest is not trusted source provenance: host adapters must
    # obtain this projection themselves. Here the digest only detects inconsistency.
    expected = projection.get('projectionHash')
    try:
        actual = digest({key: value for key, value in projection.items() if key != 'projectionHash'})
    except ModuleAnalysisError as error:
        raise ModuleSearchError(str(error)) from error
    if expected != actual:
        raise ModuleSearchError('Projection hash differs from captured facts', code='module_search_stale', status=409)
    position, edges = {unit: index for index, unit in enumerate(ids)}, []
    for edge in projection['edges']:
        if (not isinstance(edge, dict) or not isinstance(edge.get('source'), str) or not isinstance(edge.get('target'), str)
                or edge['source'] not in position or edge['target'] not in position
                or type(edge.get('count')) is not int or not 0 < edge['count'] <= 2 ** 53):
            raise ModuleSearchError('Projection dependency must have known endpoints and a positive integer count')
        edges.append((position[edge['source']], position[edge['target']], edge['count']))
    blocks, apart, limits, normalized = _constraints(ids, constraints)
    identity = {key: deepcopy(projection.get(key)) for key in (
        'version', 'snapshotId', 'projectionHash', 'sourceGraphHash', 'level', 'scope', 'coverage')}
    problem = Problem(ids, tuple(sorted(edges)), (), blocks, apart, limits, identity, normalized)
    if current_partition is None:
        raise ModuleSearchError('An explicit current partition is required')
    current = problem.labels(current_partition)
    if not problem.feasible(current):
        raise ModuleSearchError('Current partition does not satisfy hard constraints')
    return Problem(ids, problem.edges, current, blocks, apart, limits, identity, normalized)
