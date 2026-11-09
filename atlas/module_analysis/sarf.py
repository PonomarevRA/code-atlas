"""Простой направленный вариант SArF: dedication и greedy Q-дендрограмма."""

from collections import defaultdict

import networkx as nx

from .models import ModuleAnalysisError

MAX_SARF_UNITS = 200
SOURCE = 'https://arxiv.org/abs/1306.2096'


def dedication_graph(graph):
    """
    Заменяет A→B на D(A,B)=1/fanin(B), сохраняя направление и изоляты.

    Это simple SArF из III.A, не member-level DM и не доказательство переноса
    экспериментальных результатов статьи на Atlas. Вес исходных счётчиков
    не используется в этой простой формуле; fanin считает соседей, не calls.
    """
    result = nx.DiGraph()
    result.add_nodes_from(sorted(graph))
    for source, target in sorted(graph.edges()):
        if source != target:
            result.add_edge(source, target, weight=1.0 / graph.in_degree(target))
    return result


def sarf_partition(graph):
    """
    Возвращает max-Q срез направленной greedy agglomeration дендрограммы.

    Все слияния рассматриваются до одной группы; отрицательный ранний прирост
    не обрывает поиск более высокого последующего Q. Tie-break deterministic.
    Изоляты Atlas сохраняются отдельно — явная адаптация к статье.
    """
    if len(graph) > MAX_SARF_UNITS:
        raise ModuleAnalysisError(f'SArF simple budget is {MAX_SARF_UNITS} units')
    weighted = dedication_graph(graph)
    isolates = [{node} for node in sorted(nx.isolates(weighted))]
    active = sorted(set(weighted) - set(nx.isolates(weighted)))
    if not active:
        return isolates
    total = weighted.size(weight='weight')
    groups = {index: {node} for index, node in enumerate(active)}
    by_node = {node: index for index, node in enumerate(active)}
    outgoing = {index: weighted.out_degree(node, weight='weight') for node, index in by_node.items()}
    incoming = {index: weighted.in_degree(node, weight='weight') for node, index in by_node.items()}
    between = defaultdict(float)
    for source, target, data in weighted.edges(data=True):
        between[(by_node[source], by_node[target])] += data['weight']
    score = -sum(outgoing[key] * incoming[key] for key in groups) / total ** 2
    best_score, best_groups = score, [set(group) for group in groups.values()]
    while len(groups) > 1:
        identifiers = sorted(groups)
        best_pair, best_gain = None, float('-inf')
        for offset, left in enumerate(identifiers):
            for right in identifiers[offset + 1:]:
                gain = (between[left, right] + between[right, left]) / total
                gain -= (outgoing[left] * incoming[right] + outgoing[right] * incoming[left]) / total ** 2
                if gain > best_gain:
                    best_pair, best_gain = (left, right), gain
        left, right = best_pair
        for other in identifiers:
            if other in (left, right):
                continue
            between[left, other] += between[right, other]
            between[other, left] += between[other, right]
        outgoing[left] += outgoing[right]
        incoming[left] += incoming[right]
        groups[left].update(groups.pop(right))
        score += best_gain
        if score > best_score + 1e-12:
            best_score = score
            best_groups = [set(group) for _, group in sorted(groups.items())]
    return best_groups + isolates
