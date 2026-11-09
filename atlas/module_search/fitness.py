"""
Две независимые цели: явный incident MQ и точное число сменивших группу единиц.
"""

from .contracts import ModuleSearchError, check_cancel, prepare


def retained_units(current, proposed, cancelled=None):
    """
    Решает максимальное matching пересечений групп за O(k³), не завися от labels.

    Потенциалы и augmenting paths решают минимальную assignment-задачу с
    отрицательным overlap; пустые строки/столбцы дополняют прямоугольную матрицу.
    Это matching, не MoJoFM и не оценка времени рефакторинга.
    """
    size = max(max(current, default=-1), max(proposed, default=-1)) + 1
    overlaps = [[0] * size for _ in range(size)]
    for before, after in zip(current, proposed):
        overlaps[before][after] += 1
    u, v, owner, previous = [0] * (size + 1), [0] * (size + 1), [0] * (size + 1), [0] * (size + 1)
    for row in range(1, size + 1):
        check_cancel(cancelled)
        owner[0], column = row, 0
        minimum, used = [float('inf')] * (size + 1), [False] * (size + 1)
        while True:
            used[column] = True
            active_row = owner[column]
            delta, next_column = float('inf'), 0
            for candidate in range(1, size + 1):
                if used[candidate]:
                    continue
                cost = -overlaps[active_row - 1][candidate - 1] - u[active_row] - v[candidate]
                if cost < minimum[candidate]:
                    minimum[candidate], previous[candidate] = cost, column
                if minimum[candidate] < delta:
                    delta, next_column = minimum[candidate], candidate
            for candidate in range(size + 1):
                if used[candidate]:
                    u[owner[candidate]] += delta
                    v[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if owner[column] == 0:
                break
        while column:
            predecessor = previous[column]
            owner[column] = owner[predecessor]
            column = predecessor
    return sum(overlaps[owner[column] - 1][column - 1] for column in range(1, size + 1))


def score(problem, labels, cancelled=None):
    """
    Считает наблюдаемый MQ по directed counts; within-unit loops не оптимизируются.
    """
    count = max(labels, default=-1) + 1
    internal, cuts = [0] * count, [0] * count
    within_unit = 0
    for index, (source, target, weight) in enumerate(problem.edges):
        if index % 64 == 0:
            check_cancel(cancelled)
        if source == target:
            within_unit += weight
        elif labels[source] == labels[target]:
            internal[labels[source]] += weight
        else:
            cuts[labels[source]] += weight
            cuts[labels[target]] += weight
    mq = 0.0
    for inside, incident in zip(internal, cuts):
        if inside:
            mq += inside / (inside + incident / 2)
    moved = len(labels) - retained_units(problem.current, labels, cancelled)
    return {'atlasIncidentMQ': mq, 'movedUnits': moved,
            'movedFraction': moved / len(labels) if labels else 0,
            'internalBetweenUnitWeight': sum(internal), 'cutWeight': sum(cuts) // 2,
            'withinUnitWeight': within_unit, 'groupCount': count}


def dominates(left, right):
    """
    Сравнивает только объявленные цели без скрытого scalar score или epsilon.
    """
    quality = left['atlasIncidentMQ'] >= right['atlasIncidentMQ']
    change = left['movedUnits'] <= right['movedUnits']
    strict = left['atlasIncidentMQ'] > right['atlasIncidentMQ'] or left['movedUnits'] < right['movedUnits']
    return quality and change and strict


def evaluate_partition(projection, partition, current_partition, *, constraints=None):
    """
    Независимо оценивает один явный baseline теми же целями и hard constraints.
    """
    problem = prepare(projection, current_partition, constraints)
    labels = problem.labels(partition)
    if not problem.feasible(labels):
        raise ModuleSearchError('Partition violates hard constraints')
    return {'source': problem.identity, 'partition': problem.partition(labels), 'objectives': score(problem, labels)}
