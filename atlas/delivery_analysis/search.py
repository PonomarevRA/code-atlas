"""Ограниченные независимые способы поиска и явно TC-inspired crossover."""

import random

from .evaluation import evaluate_delivery
from .model import DeliveryAnalysisError, dependency_components, prepare_problem

MAX_EXACT_UNITS = 8
MAX_EVALUATIONS = 4096


def _units(problem, condense):
    """Выбирает неделимые SCC либо file encoding для явно обозначенной абляции."""
    components = dependency_components(problem)
    return components if condense else tuple((file,) for group in components for file in group)


def _assignment(units, genome):
    """Раскрывает компонентные гены в полное file→module назначение."""
    return {file: 'm' + str(label) for group, label in zip(units, genome) for file in group}


def _canonical(genome):
    """Убирает произвольную перенумерацию модулей, сохраняя состав групп."""
    mapping, result = {}, []
    for label in genome:
        if label not in mapping:
            mapping[label] = len(mapping)
        result.append(mapping[label])
    return tuple(result)


def _partitions(size, module_limit, prefix=()):
    """Перечисляет restricted-growth strings, каждое разбиение ровно один раз."""
    if len(prefix) == size:
        yield prefix
        return
    allowed = min(module_limit, max(prefix, default=-1) + 2)
    for label in range(allowed):
        yield from _partitions(size, module_limit, prefix + (label,))


def exact_partition_oracle(problem, *, condense=True):
    """
    Возвращает точный минимум для не более восьми SCC/file units и k модулей.

    Это exhaustive oracle ограниченной синтетической модели, не MILP из статьи.
    Корректность самого evaluator дополнительно проверяется независимым bitset
    oracle и ручными ожидаемыми поставками в tests.
    """
    problem = prepare_problem(problem)
    if type(condense) is not bool:
        raise DeliveryAnalysisError('condense must be a boolean')
    units = _units(problem, condense)
    if len(units) > MAX_EXACT_UNITS:
        raise DeliveryAnalysisError(f'Exact oracle limited to {MAX_EXACT_UNITS} components/files')
    best, evaluated = None, 0
    for genome in _partitions(len(units), problem.module_limit):
        result = evaluate_delivery(problem, _assignment(units, genome), require_scc=condense)
        evaluated += 1
        if best is None or result['objective'] < best['objective']:
            best = result
    return {'method': 'exact-enumeration', 'problemHash': problem.problem_hash,
            'condensed': condense, 'unitCount': len(units), 'partitionsEvaluated': evaluated,
            'optimal': True, 'best': best}


def baseline_assignments(problem):
    """Даёт простые SCC-согласованные all-in-one и round-robin отправные точки."""
    problem = prepare_problem(problem)
    units = dependency_components(problem)
    return {'all-in-one': _assignment(units, [0] * len(units)),
            'scc-round-robin': _assignment(units, [index % problem.module_limit for index in range(len(units))])}


class _EvaluationBudget:
    """Один владелец ограничивает ВСЕ полные objective evaluations одного запуска."""

    def __init__(self, problem, units, condense, maximum):
        """Сохраняет immutable вход и счётчик; нет скрытого cache/retry."""
        self.problem, self.units, self.condense = problem, units, condense
        self.maximum, self.used = maximum, 0
        self.best = None

    def evaluate(self, genome):
        """Возвращает результат либо None при исчерпанном hard budget."""
        if self.used >= self.maximum:
            return None
        self.used += 1
        result = evaluate_delivery(self.problem, _assignment(self.units, genome), require_scc=self.condense)
        if self.best is None or result['objective'] < self.best['objective']:
            self.best = result
        return result


def _child(left, right, operator, rng, budget):
    """
    Скрещивает две полные последовательности, не создавая частично поставимого кода.

    TC-inspired оставляет будущие гены от left, а для текущего гена сравнивает
    два полных completion. Это намеренно НЕ COST/CONSISTENT из Algorithm1.
    При нехватке budget возвращает None, не выдавая неоценённый результат.
    """
    if operator == 'uniform':
        return tuple(left[index] if rng.random() < .5 else right[index] for index in range(len(left)))
    child = list(left)
    for index in range(len(child)):
        if left[index] == right[index]:
            continue
        choices = []
        for label in (left[index], right[index]):
            candidate = list(child)
            candidate[index] = label
            result = budget.evaluate(_canonical(candidate))
            if result is None:
                return None
            choices.append((result['objective'], label))
        best = min(value for value, _ in choices)
        candidates = [label for value, label in choices if value == best]
        child[index] = rng.choice(candidates)
    return _canonical(child)


def _validate_search(seed, population, generations, evaluation_budget):
    """Отклоняет неверные бюджеты до запуска evaluator или exhaustive oracle."""
    parameters = ((seed, 0, 2 ** 32 - 1), (population, 2, 12), (generations, 1, 200),
                  (evaluation_budget, population, MAX_EVALUATIONS))
    if any(type(value) is not int or not low <= value <= high for value, low, high in parameters):
        raise DeliveryAnalysisError('Invalid bounded search parameters')


def genetic_search(problem, *, condense=True, operator='uniform', seed=42,
                   population=4, generations=100, evaluation_budget=256):
    """
    Ищет ограниченный синтетический вариант с одинаковым жёстким budget вызовов.

    Uniform и TC-inspired имеют разные затраты на ребёнка, поэтому считаются
    также ВСЕ промежуточные evaluations. Результат не обещает оптимальность.
    """
    _validate_search(seed, population, generations, evaluation_budget)
    problem = prepare_problem(problem)
    if type(condense) is not bool or not isinstance(operator, str) or operator not in {'uniform', 'tc-inspired'}:
        raise DeliveryAnalysisError('Unknown encoding/operator')
    rng = random.Random(seed)
    units = _units(problem, condense)
    budget = _EvaluationBudget(problem, units, condense, evaluation_budget)
    genomes = [tuple(0 for _ in units), tuple(index % problem.module_limit for index in range(len(units)))]
    while len(genomes) < population:
        genomes.append(_canonical([rng.randrange(problem.module_limit) for _ in units]))
    ranked = []
    for genome in genomes:
        result = budget.evaluate(genome)
        ranked.append((result['objective'], genome, result))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    best = ranked[0][2]
    history = [best['objective']]
    completed = 0
    for generation in range(generations):
        descendants = []
        while len(descendants) < population:
            left, right = ranked[rng.randrange(min(len(ranked), 2))][1], ranked[rng.randrange(min(len(ranked), 2))][1]
            child = _child(left, right, operator, rng, budget)
            if child is None:
                break
            # Мутация может открыть другой module label. Здесь нет исправления
            # реального исходного кода или скрытого запуска сборки проекта.
            child = list(child)
            if rng.random() < .1:
                child[rng.randrange(len(child))] = rng.randrange(problem.module_limit)
            child = _canonical(child)
            result = budget.evaluate(child)
            if result is None:
                break
            descendants.append((result['objective'], child, result))
        if descendants:
            ranked = sorted(ranked + descendants, key=lambda entry: (entry[0], entry[1]))[:population]
            if ranked[0][0] < best['objective']:
                best = ranked[0][2]
            history.append(best['objective'])
            completed = generation + 1
        if budget.used >= budget.maximum or not descendants:
            break
    # Полное оценённое completion сохраняется, даже если для окончания
    # последнего crossover не хватило budget: известный лучший вариант не теряется.
    if budget.best['objective'] < best['objective']:
        best = budget.best
        history.append(best['objective'])
    return {'method': 'bounded-genetic-search', 'problemHash': problem.problem_hash,
            'condensed': condense, 'operator': operator, 'seed': seed,
            'population': population, 'generationLimit': generations, 'generationsCompleted': completed,
            'evaluationLimit': evaluation_budget, 'evaluationsUsed': budget.used,
            'stopReason': 'evaluation-budget' if budget.used == budget.maximum else 'generation-limit',
            'unitCount': len(units), 'history': history, 'best': best, 'optimal': False,
            'operatorClaim': 'TC-inspired full-completion heuristic; not exact published TC-LM' if operator == 'tc-inspired' else 'uniform per-gene choice',
            'limitations': ['Encoding changes search space; factorial results do not isolate a pure causal effect.',
                            'Depends-on convention and fixed-point delivery are explicit Atlas adaptations.',
                            'Same evaluation limit/seed; inspect actual evaluations and stop reason before comparison.']}


def compare_search(problem, *, seed=42, population=4, generations=100, evaluation_budget=256):
    """Сохраняет все четыре ablation результата и gap к bounded exact oracle."""
    _validate_search(seed, population, generations, evaluation_budget)
    problem = prepare_problem(problem)
    outcomes = []
    for condense in (False, True):
        try:
            oracle = exact_partition_oracle(problem, condense=condense)
        except DeliveryAnalysisError as error:
            oracle = {'method': 'exact-enumeration', 'condensed': condense, 'status': 'unavailable', 'reason': str(error)}
        for operator in ('uniform', 'tc-inspired'):
            result = genetic_search(problem, condense=condense, operator=operator, seed=seed,
                                    population=population, generations=generations, evaluation_budget=evaluation_budget)
            result['oracle'] = oracle
            result['gapToExact'] = result['best']['objective'] - oracle['best']['objective'] if 'best' in oracle else None
            outcomes.append(result)
    baselines = {key: evaluate_delivery(problem, value) for key, value in baseline_assignments(problem).items()}
    return {'version': 'atlas-delivery-comparison/v1', 'problemHash': problem.problem_hash,
            'syntheticOnly': True, 'baselines': baselines, 'outcomes': outcomes,
            'paperReproduction': False,
            'interpretation': 'Count implemented requirements per configuration. Neither byte size nor graph modularity; no real repository benefit claim.'}
