"""
Собственные bounded операторы над неделимыми must-link компонентами.
"""

from .contracts import canonical_labels


def random_candidate(problem, rng):
    """
    Выбирает categorical assignment; это не uniform sampling множества разбиений.
    """
    count = rng.randint(max(1, problem.limits[2]), max(1, min(len(problem.blocks), problem.limits[3])))
    labels = [0] * len(problem.ids)
    for block in problem.blocks:
        label = rng.randrange(count)
        for member in block:
            labels[member] = label
    return canonical_labels(labels)


def mutate(problem, labels, rng):
    """
    Перемещает одну неделимую компоненту в существующую или новую группу.
    """
    changed = list(labels)
    block = rng.choice(problem.blocks)
    destination = rng.randrange(max(labels) + 2)
    for member in block:
        changed[member] = destination
    return canonical_labels(changed)


def crossover(problem, first, second, rng):
    """
    Наследует одну целую группу first, остальные группы second; не TC-LM/TwoArchive.
    """
    selected = rng.choice(sorted(set(first)))
    labels = [('first', selected) if label == selected else ('second', second[index])
              for index, label in enumerate(first)]
    return canonical_labels(labels)
