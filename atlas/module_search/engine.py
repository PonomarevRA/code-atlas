"""
Сравнимые ограниченные поиски с единым fitness и provenance конечного frontier.
"""

from copy import deepcopy
import json
import random

from atlas.module_analysis.models import digest

from .contracts import (Cancelled, MAX_EVALUATIONS, MAX_PROPOSALS, ModuleSearchError,
                        bounded_int, check_cancel, prepare)
from .fitness import dominates, score
from .operators import crossover, mutate, random_candidate

ALGORITHM = 'atlas-pareto-ga/v1'
ARMS = ('genetic', 'random', 'local')


def _insert(archive, labels, metrics):
    """
    Сохраняет весь недоминируемый набор в пределах evaluation cap, включая ties.
    """
    if labels in archive or any(dominates(value, metrics) for value in archive.values()):
        return
    for old in list(archive):
        if dominates(metrics, archive[old]):
            del archive[old]
    archive[labels] = metrics


def _proposal(problem, labels, metrics, origins):
    """
    Создаёт самостоятельное предложение с исходными IDs и явной identity.
    """
    partition = problem.partition(labels)
    return {'proposalId': digest({'projectionHash': problem.identity['projectionHash'], 'partition': partition})[:24],
            'partition': partition, 'objectives': dict(metrics), 'origins': sorted(origins)}


def _baseline_labels(problem, supplied):
    """
    Проверяет внешние разбиения до вычислений и канонизирует input identity.
    """
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, dict) or set(supplied) - {'sarf', 'leiden'}:
        raise ModuleSearchError('baselines supports only explicit sarf/leiden partitions')
    return {name: problem.labels(partition) for name, partition in sorted(supplied.items()) if partition is not None}


def _baselines(problem, supplied, current_metrics):
    """
    Оценивает явно переданные baselines; неизвестный upstream generation cost не скрыт.
    """
    records = {'current': {'status': 'evaluated', 'objectives': current_metrics,
                           'partition': problem.partition(problem.current), 'objectiveEvaluations': 1}}
    evaluated = []
    for name in ('sarf', 'leiden'):
        if supplied.get(name) is None:
            records[name] = {'status': 'unavailable', 'reason': 'partition-not-supplied', 'objectiveEvaluations': 0}
            continue
        labels = supplied[name]
        if not problem.feasible(labels):
            records[name] = {'status': 'infeasible', 'reason': 'hard-constraints', 'objectiveEvaluations': 0}
            continue
        metrics = score(problem, labels)
        evaluated.append((name, labels, metrics))
        records[name] = {'status': 'evaluated', 'objectives': metrics, 'partition': problem.partition(labels),
                         'objectiveEvaluations': 1, 'upstreamGenerationCost': 'not-measured-by-this-call',
                         'algorithmQualification': 'caller-supplied partition; provider identity not verified'}
    return records, evaluated


def _run(problem, budget, seed, baseline_metrics, cancelled):
    """
    Interleave три arms с одинаковыми caps; cancellation оставляет только полные оценки.
    """
    states = {}
    for index, name in enumerate(ARMS):
        states[name] = {'rng': random.Random(seed + index * 104729), 'archive': {problem.current: baseline_metrics},
                        'attempts': 0, 'evaluations': 0, 'rejected': 0, 'stopReason': 'evaluation-cap'}
    observed = {problem.current: {'metrics': baseline_metrics, 'origins': {'current'}}}
    was_cancelled = False
    try:
        for _ in range(4 * budget):
            for name in ARMS:
                check_cancel(cancelled)
                state = states[name]
                if state['evaluations'] >= budget:
                    continue
                state['attempts'] += 1
                population = sorted(state['archive'])
                rng = state['rng']
                if name == 'random':
                    candidate = random_candidate(problem, rng)
                else:
                    first = rng.choice(population)
                    if name == 'genetic' and len(population) > 1:
                        first = crossover(problem, first, rng.choice(population), rng)
                    candidate = mutate(problem, first, rng)
                if not problem.feasible(candidate):
                    state['rejected'] += 1
                    continue
                # Duplicates cost an evaluation too: no arm receives free cache hits.
                metrics = score(problem, candidate, cancelled)
                state['evaluations'] += 1
                _insert(state['archive'], candidate, metrics)
                entry = observed.setdefault(candidate, {'metrics': metrics, 'origins': set()})
                entry['origins'].add(name)
            if all(state['evaluations'] >= budget for state in states.values()):
                break
    except Cancelled:
        was_cancelled = True
    for state in states.values():
        if was_cancelled:
            state['stopReason'] = 'cancelled'
        elif state['evaluations'] < budget:
            state['stopReason'] = 'attempt-cap'
    return states, observed, was_cancelled


def search_partitions(projection, current_partition, *, constraints=None, seed=0,
                      evaluations_per_method=64, max_proposals=16, baselines=None, cancelled=None):
    """
    Выдаёт finite-evaluated Pareto proposals с hard constraints без изменения входа.

    GA/random/local получают одинаковые evaluation/attempt caps и current seed.
    Внешние SArF/Leiden только оцениваются; их generation budget сюда не входит.
    Максимум128 units/10000 edges/256 evaluations на arm; output<=16 proposals.
    Reproducibility относится к этой версии Python/алгоритма при том же seed.
    """
    bounded_int(seed, 'seed', 0, 2 ** 32 - 1)
    budget = bounded_int(evaluations_per_method, 'evaluations_per_method', 1, MAX_EVALUATIONS)
    limit = bounded_int(max_proposals, 'max_proposals', 1, MAX_PROPOSALS)
    if cancelled is not None and not callable(cancelled):
        raise ModuleSearchError('cancelled must be a cooperative callback')
    problem = prepare(projection, current_partition, constraints)
    baseline_labels = _baseline_labels(problem, baselines)
    stopped = bool(cancelled and cancelled())
    if stopped:
        current_metrics, evaluated = None, []
        records = {name: {'status': 'not-evaluated-cancelled', 'objectiveEvaluations': 0}
                   for name in ('current', 'sarf', 'leiden')}
    else:
        current_metrics = score(problem, problem.current)
        records, evaluated = _baselines(problem, baseline_labels, current_metrics)
    if problem.ids and not stopped:
        states, observed, stopped = _run(problem, budget, seed, current_metrics, cancelled)
    else:
        states, observed = {}, {}
    # External baselines never seed just one arm or secretly consume its budget.
    for name, labels, metrics in evaluated:
        observed.setdefault(labels, {'metrics': metrics, 'origins': set()})['origins'].add(name)
    frontier = {}
    for labels, entry in sorted(observed.items()):
        _insert(frontier, labels, entry['metrics'])
    ordered = sorted(frontier, key=lambda labels: (frontier[labels]['movedUnits'], -frontier[labels]['atlasIncidentMQ'], labels))
    proposals = [_proposal(problem, labels, frontier[labels], observed[labels]['origins']) for labels in ordered[:limit]]
    counters = {}
    for name, state in states.items():
        counters[name] = {key: state[key] for key in ('attempts', 'evaluations', 'rejected', 'stopReason')}
        counters[name]['frontierObjectiveVectors'] = sorted({(m['atlasIncidentMQ'], m['movedUnits']) for m in state['archive'].values()})
    inputs = {'source': problem.identity, 'current': problem.partition(problem.current), 'constraints': problem.constraints,
              'seed': seed, 'evaluationsPerMethod': budget, 'maxProposals': limit,
              'baselines': {name: problem.partition(labels) for name, labels in baseline_labels.items()}}
    result = {'version': 'module-search-v1', 'algorithm': ALGORITHM, 'source': deepcopy(problem.identity),
            'inputHash': digest(inputs), 'status': 'cancelled' if stopped else 'completed' if problem.ids else 'no-units',
            'constraints': deepcopy(problem.constraints), 'seed': seed,
            'budgets': {'evaluationsPerMethod': budget, 'attemptsPerMethod': 4 * budget,
                        'baselineObjectiveEvaluations': sum(record['objectiveEvaluations'] for record in records.values()),
                        'fairness': 'Equal GA/random/local caps and shared current seed; external generation costs separate.'},
            'methods': counters, 'baselines': records, 'proposals': proposals,
            'frontierSize': len(frontier), 'truncated': len(frontier) > len(proposals),
            'objectiveDefinitions': {'atlasIncidentMQ': 'maximize sum internal/(internal+0.5*(incomingCut+outgoingCut)); zero if internal=0; inter-unit edges only',
                                     'movedUnits': 'minimize N minus exact maximum-overlap matching of groups; not MoJoFM'},
            'qualification': 'bounded-offline-adaptation; consumer-not-assessed',
            'limitations': ['Finite evaluated frontier, not global Pareto optimality.',
                            'Static MQ and membership changes do not prove semantic cohesion or refactoring effort.',
                            'Partial coverage remains partial; unknown dependencies can change objectives in either direction.',
                            'No raw graph, ownership, source files, defaults or geometry changed.',
                            'Random categorical assignments are not uniform random partitions; no exact Two-Archive/NSGA reproduction.']}
    # Keep whole proposals; a cut partition would violate exact unit coverage.
    # Leave headroom for the host's project/request envelope under MCP's 64k cap.
    while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 60_000 and proposals:
        proposals.pop()
        result['truncated'] = True
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 60_000:
        raise ModuleSearchError('Search identity/baselines exceed response budget; select a smaller scope',
                                code='module_search_budget', status=413)
    return result
