"""Непересекающееся владение символами и направленная проекция class/folder."""

from collections import defaultdict
from pathlib import PurePosixPath

from ..code_health_calls import confirmed_call_edge
from .models import (CLASS_KINDS, CONTAINMENT, DEPENDENCIES, MAX_NODES, MAX_EDGES, MAX_UNITS, VERSION,
                     ModuleAnalysisError, digest, project_path, validate_graph)


def _class_node(node):
    """Признаёт тип только по явному kind-факту, а не по имени или подписи."""
    return node.get('kind') in CLASS_KINDS or (node.get('declarationFacts') or {}).get('kind') in CLASS_KINDS


def _class_owners(nodes, edges, supplied):
    """
    Находит ближайшего владельца-класс; неоднозначность и циклы остаются неизвестными.

    Явная caller mapping отделена от захваченной идентичности типа.
    """
    parents = defaultdict(set)
    for edge in edges:
        if (edge.get('relation') or edge.get('type')) in CONTAINMENT and confirmed_call_edge(edge, nodes.get(edge['source'])):
            parents[edge['target']].add(edge['source'])
    classes = {key for key, node in nodes.items() if _class_node(node)}
    identities = {}
    for key in classes:
        identity = nodes[key].get('classIdentity')
        identities[key] = supplied.get(key) or (identity if isinstance(identity, str) and identity else key)
    owners, reasons = {}, {}
    for key, node in sorted(nodes.items()):
        if key in supplied:
            owners[key] = (supplied[key], 'caller-supplied')
            continue
        if key in classes:
            owners[key] = (identities[key], 'captured-class-identity')
            continue
        declared = (node.get('memberFacts') or {}).get('ownerNodeId')
        if declared in classes:
            owners[key] = (identities[declared], 'caller-supplied' if declared in supplied else 'captured-member-owner')
            continue
        frontier, visited, found = set(parents[key]), {key}, set()
        while frontier and not found:
            next_frontier = set()
            for parent in sorted(frontier - visited):
                visited.add(parent)
                if parent in classes:
                    found.add(identities[parent])
                else:
                    next_frontier.update(parents[parent])
            frontier = next_frontier - visited
        if len(found) == 1:
            owners[key] = (next(iter(found)), 'captured-containment')
        else:
            reasons[key] = 'ambiguous-class-owner' if found else 'class-owner-unavailable'
    return owners, reasons


def _folder_frontier(folders):
    """Отклоняет пересекающиеся выбранные папки вместо двойного владения узлами."""
    if folders is None:
        return None
    if not isinstance(folders, list) or not folders:
        raise ModuleAnalysisError('folders must be a nonempty array')
    values = [project_path(value, folder=True) for value in folders]
    if len(set(values)) != len(values):
        raise ModuleAnalysisError('Duplicate selected folder')
    for index, value in enumerate(values):
        for other in values[index + 1:]:
            if value == '.' or other == '.' or value.startswith(other + '/') or other.startswith(value + '/'):
                raise ModuleAnalysisError('Selected folders overlap; choose one frontier')
    return sorted(values)


def _coverage(graph, unknown, uncertain, missing):
    """Не объявляет полноту, если источник её явно не подтвердил."""
    metadata = graph.get('metadata') or {}
    state = graph.get('analysisStatus') or metadata.get('analysisStatus')
    complete = state == 'complete' and not graph.get('truncated') and not metadata.get('truncated')
    reasons = []
    if not complete:
        reasons.append('source-analysis-not-confirmed-complete')
    if unknown:
        reasons.append('ownership-incomplete')
    if uncertain or missing:
        reasons.append('dependency-binding-incomplete')
    return {'status': 'complete' if not reasons else 'partial', 'sourceAnalysisStatus': state or 'unknown',
            'unknownOwnershipCount': len(unknown), 'uncertainDependencyCount': uncertain,
            'missingEndpointCount': missing, 'unknownReasons': reasons}


def _assign_units(candidates, level, frontier, class_owners, class_unknown):
    """
    Назначает ровно одного владельца и собирает исходные IDs/файлы по единице.

    Неразрешённые и внешние выбранному frontier узлы возвращаются отдельно.
    """
    ownership_by_id, units, unknown, outside = {}, {}, [], []
    for key, node in sorted(candidates.items()):
        filename = node.get('file') or node.get('source_file')
        try:
            filename = project_path(filename)
        except ModuleAnalysisError:
            filename = None
        if level == 'folder':
            if filename is None:
                unknown.append({'rawId': key, 'reason': 'source-file-unavailable'})
                continue
            directory = str(PurePosixPath(filename).parent)
            chosen = directory
            if frontier is not None:
                chosen = next((folder for folder in frontier if folder == '.' or directory == folder or directory.startswith(folder + '/')), None)
                if chosen is None:
                    outside.append(key)
                    continue
            identity, origin = chosen, 'captured-file-path'
        else:
            if key not in class_owners:
                unknown.append({'rawId': key, 'reason': class_unknown.get(key, 'class-owner-unavailable')})
                continue
            identity, origin = class_owners[key]
        unit_id = level + ':' + digest(identity)[:24]
        ownership_by_id[key] = unit_id
        unit = units.setdefault(unit_id, {'id': unit_id, 'kind': level, 'label': identity, 'sourceIds': [], 'files': set(), 'ownershipSources': set()})
        unit['sourceIds'].append(key)
        unit['ownershipSources'].add(origin)
        if filename:
            unit['files'].add(filename)
    if len(ownership_by_id) > MAX_NODES or len(units) > MAX_UNITS:
        advice = 'use folder level with a bounded folder scope for a large map' if level == 'class' else 'select a smaller folder scope'
        raise ModuleAnalysisError(f'Module budget exceeded: {MAX_NODES} source nodes / {MAX_UNITS} units; {advice}',
                                  code='module_analysis_budget', status=413)
    return ownership_by_id, units, unknown, outside


def _project_dependencies(edges, nodes, ownership_by_id, frontier, outside):
    """
    Агрегирует направленные факты после проверки scope, confidence и source hash.

    Точная копия факта учитывается один раз; неизвестные endpoints не исчезают
    из coverage, а containment не превращается в программную зависимость.
    """
    dependencies, raw_seen, uncertain, missing, external, ignored = {}, set(), 0, 0, 0, 0
    outside_set, scoped_facts = set(outside), 0
    for edge in edges:
        relation = edge.get('relation') or edge.get('type') or ''
        if relation not in DEPENDENCIES:
            ignored += 1
            continue
        source, target = edge['source'], edge['target']
        if frontier is not None and source not in ownership_by_id and target not in ownership_by_id:
            continue
        scoped_facts += 1
        if scoped_facts > MAX_EDGES:
            raise ModuleAnalysisError(f'Dependency budget exceeded: {MAX_EDGES} selected/boundary facts',
                                      code='module_analysis_budget', status=413)
        fingerprint = digest(edge)
        if fingerprint in raw_seen:
            continue
        raw_seen.add(fingerprint)
        if source not in nodes or target not in nodes:
            missing += 1
            continue
        if not confirmed_call_edge(edge, nodes.get(source)):
            uncertain += 1
            continue
        if nodes[source].get('kind') == 'external' or nodes[target].get('kind') == 'external':
            external += 1
            continue
        if source in outside_set or target in outside_set:
            external += 1
            continue
        if source not in ownership_by_id or target not in ownership_by_id:
            uncertain += 1
            continue
        start, end = ownership_by_id[source], ownership_by_id[target]
        record = dependencies.setdefault((start, end, relation), {'source': start, 'target': end, 'relation': relation, 'count': 0, 'rawEdges': []})
        record['count'] += 1
        record['rawEdges'].append({'source': source, 'target': target, 'edgeHash': fingerprint})
    return dependencies, uncertain, missing, external, ignored


def project_modules(graph, *, level='folder', folders=None, ownership=None, snapshot_id=None):
    """
    Строит ограниченную source-bound проекцию без изменения исходного raw-графа.

    Folder mode использует прямую папку файла либо явный непересекающийся frontier.
    Class mode требует захваченного kind/owner либо явно помеченной caller mapping.
    При неполных данных лишь абсолютные счётчики доверенных зависимостей
    могут быть нижними границами при фиксированных области и владельцах;
    проекция не задаёт границы для Q/плотности и не доказывает полноту.
    """
    if level not in {'class', 'folder'}:
        raise ModuleAnalysisError('level must be class or folder')
    if snapshot_id is not None and (not isinstance(snapshot_id, str) or not 0 < len(snapshot_id) <= 128):
        raise ModuleAnalysisError('snapshot_id must be a bounded string')
    frontier = _folder_frontier(folders)
    try:
        nodes = validate_graph(graph, scoped=frontier is not None)
    except ModuleAnalysisError as error:
        if error.status == 413 and level == 'class':
            raise ModuleAnalysisError(
                f'Class analysis is a bounded pilot ({MAX_NODES} source nodes / {MAX_EDGES} edges); '
                'use folder level with a bounded folder scope for a large map',
                code='module_analysis_budget', status=413) from None
        raise
    if ownership is not None and (level != 'class' or not isinstance(ownership, dict)):
        raise ModuleAnalysisError('ownership is a raw-ID to class-identity mapping for class mode')
    supplied = ownership or {}
    for raw_id, identity in supplied.items():
        if raw_id not in nodes or not isinstance(identity, str) or not 0 < len(identity) <= 1024:
            raise ModuleAnalysisError('Invalid explicit class ownership')
    if level == 'class' and folders is not None:
        raise ModuleAnalysisError('folders applies only to folder mode')
    candidates = {key: node for key, node in nodes.items() if node.get('kind') not in {'external', 'diagnostic', 'file-diagnostic', 'analysis-diagnostic'}}
    class_owners, class_unknown = _class_owners(candidates, graph['edges'], supplied) if level == 'class' else ({}, {})
    ownership_by_id, units, unknown, outside = _assign_units(candidates, level, frontier, class_owners, class_unknown)
    dependencies, uncertain, missing, external, ignored = _project_dependencies(
        graph['edges'], nodes, ownership_by_id, frontier, outside)
    normalized_units = []
    for _, unit in sorted(units.items()):
        normalized_units.append({**unit, 'files': sorted(unit['files']), 'ownershipSources': sorted(unit['ownershipSources'])})
    normalized_edges = []
    for _, edge in sorted(dependencies.items()):
        edge['rawEdges'].sort(key=lambda value: value['edgeHash'])
        normalized_edges.append(edge)
    source_hash = digest({'nodes': sorted(graph['nodes'], key=lambda node: node['id']), 'edges': sorted(graph['edges'], key=digest)})
    result = {'version': VERSION, 'level': level, 'snapshotId': snapshot_id or graph.get('snapshotId'),
              'sourceGraphHash': source_hash, 'scope': {'folders': frontier, 'ownershipSource': 'caller-supplied' if supplied else 'captured'},
              'units': normalized_units, 'edges': normalized_edges, 'ownership': dict(sorted(ownership_by_id.items())),
              'unknownOwnership': unknown[:256], 'unknownOwnershipTruncated': len(unknown) > 256,
              'outsideScopeIds': sorted(outside)[:256], 'outsideScopeCount': len(outside),
              'outsideScopeIdsTruncated': len(outside) > 256,
              'coverage': _coverage(graph, unknown, uncertain, missing),
              'relationEvidence': 'Captured static facts; shared confidence/source-hash gate rejects explicit uncertainty but does not establish semantic binding.',
              'counts': {'rawNodes': len(nodes), 'projectedNodes': len(ownership_by_id), 'units': len(units),
                         'dependencyFacts': sum(edge['count'] for edge in normalized_edges),
                         'externalDependencies': external, 'ignoredNonDependencyEdges': ignored},
              'limitations': ['Static dependencies do not establish semantic cohesion.', 'Containment is not a dependency.']}
    result['projectionHash'] = digest(result)
    return result
