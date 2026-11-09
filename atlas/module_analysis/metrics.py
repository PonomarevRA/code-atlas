"""Объяснимые наблюдаемые метрики; архитектурные оценки не подменяются Q."""

import networkx as nx

from .models import VERSION, validate_partition


def dependency_graph(module_graph, *, directed=True, internal=False):
    """
    Строит weighted граф из захваченных фактов, явно выбирая проекцию направления.

    Межмодульные алгоритмы не получают self loops внутримодульных raw-вызовов.
    """
    graph = nx.DiGraph() if directed else nx.Graph()
    graph.add_nodes_from(unit['id'] for unit in module_graph['units'])
    for edge in module_graph['edges']:
        source, target = edge['source'], edge['target']
        if source == target and not internal:
            continue
        previous = graph.get_edge_data(source, target, {}).get('weight', 0)
        graph.add_edge(source, target, weight=previous + edge['count'])
    return graph


def assess_partition(module_graph, partition=None):
    """
    Оценивает полное разбиение по наблюдаемым связям без общего «балла качества».

    cohesionDensity — доля направленных пар единиц со связью внутри группы,
    не семантическая целостность. При partial coverage абсолютные счётчики
    доверенных зависимостей — нижние границы только для той же фиксированной
    области и владельцев. Q, плотность и другие отношения — наблюдаемые
    значения, не математические границы; скрытые связи могут менять их в обе
    стороны. Отсутствие циклов и полная оценка остаются unknown.
    """
    groups = validate_partition(module_graph, partition)
    graph = dependency_graph(module_graph)
    assignment = {member: index for index, group in enumerate(groups) for member in group}
    source_counts = {unit['id']: len(unit['sourceIds']) for unit in module_graph['units']}
    within_unit_weight = sum(edge['count'] for edge in module_graph['edges'] if edge['source'] == edge['target'])
    internal_weight, external_weight = 0, 0
    incoming, outgoing, pair_count = [0] * len(groups), [0] * len(groups), [0] * len(groups)
    for source, target, data in graph.edges(data=True):
        origin, destination = assignment[source], assignment[target]
        weight = data['weight']
        if origin == destination:
            internal_weight += weight
            pair_count[origin] += 1
        else:
            external_weight += weight
            outgoing[origin] += weight
            incoming[destination] += weight
    components = [sorted(members) for members in nx.strongly_connected_components(graph) if len(members) > 1]
    complete = module_graph['coverage']['status'] == 'complete'
    summaries = []
    for index, group in enumerate(groups):
        size = len(group)
        summaries.append({'members': group, 'unitCount': size,
                          'sourceNodeCount': sum(source_counts[member] for member in group),
                          'incomingWeight': incoming[index], 'outgoingWeight': outgoing[index],
                          'cohesionDensity': pair_count[index] / (size * (size - 1)) if size > 1 else None,
                          'weaklyConnectedObserved': nx.is_weakly_connected(graph.subgraph(group)) if size else None})
    modularity = nx.community.modularity(graph, [set(group) for group in groups], weight='weight') if graph.size(weight='weight') else None
    return {'version': VERSION, 'projectionHash': module_graph['projectionHash'],
            'snapshotId': module_graph['snapshotId'], 'status': 'observed' if complete else 'partial',
            'coverage': module_graph['coverage'], 'groups': summaries,
            'metrics': {'internalDependencyWeight': internal_weight + within_unit_weight,
                        'internalBetweenUnitDependencyWeight': internal_weight,
                        'withinUnitDependencyWeight': within_unit_weight,
                        'externalDependencyWeight': external_weight,
                        'scopeBoundaryDependencyFacts': module_graph['counts']['externalDependencies'],
                        'groupCount': len(groups), 'minGroupUnits': min(map(len, groups), default=0),
                        'maxGroupUnits': max(map(len, groups), default=0), 'observedCycleComponents': sorted(components),
                        'absenceOfCycles': not components if complete else None,
                        'directedModularity': modularity},
            'interpretation': 'Q/density are observed values, not bounds under partial coverage. Only absolute accepted dependency counts can be lower bounds for fixed scope/ownership. Q/density use inter-unit edges; within-unit dependencies are separate. Cycles refer to selected scope. No semantic or H/O/T grade.'}
