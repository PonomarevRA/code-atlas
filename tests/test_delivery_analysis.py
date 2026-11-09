"""Ручные комплектации и независимый bitset oracle offline-прототипа поставки."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from atlas.delivery_analysis import (DeliveryAnalysisError, baseline_assignments,
                                     compare_search, dependency_components,
                                     evaluate_delivery, exact_partition_oracle,
                                     genetic_search, prepare_problem)


def case():
    """Три файла, две комплектации и два доступных модуля; ручной optimum=3."""
    return {'schemaVersion': 'atlas-delivery-problem/v1', 'source': 'declared-synthetic',
            'files': ['a', 'b', 'c'], 'dependencies': [['a', 'b']],
            'requirements': {'ra': ['a'], 'rb': ['b'], 'rc': ['c']},
            'configurations': {'a-only': ['ra'], 'c-only': ['rc']}, 'moduleLimit': 2}


def bitset_oracle(data, labels):
    """
    Независимо замыкает булевы bitsets попарными правилами, не вызывает evaluator.

    В отличие от production pending-stack перебирает каждую возможную дугу
    и модульную пару до неподвижной точки; затем проверяет all-files masks.
    """
    files = sorted(data['files'])
    bit = {file: 1 << index for index, file in enumerate(files)}
    rows, total = {}, 0
    for config, wanted in data['configurations'].items():
        mask = 0
        for requirement in wanted:
            for file in data['requirements'][requirement]:
                mask |= bit[file]
        previous = None
        while previous != mask:
            previous = mask
            for source in files:
                for target in files:
                    linked = labels[source] == labels[target] or [source, target] in data['dependencies']
                    if linked and mask & bit[source]:
                        mask |= bit[target]
        implemented = []
        for requirement, members in data['requirements'].items():
            requirement_mask = 0
            for file in members:
                requirement_mask |= bit[file]
            if mask & requirement_mask == requirement_mask:
                implemented.append(requirement)
        rows[config] = {'files': sorted(file for file in files if mask & bit[file]),
                        'requirements': sorted(implemented)}
        total += len(implemented)
    return total, rows


def test_known_manual_delivery_and_exact_optimum():
    """{a,b}/{c} даёт2+1=3; единый модуль даёт3+3=6."""
    problem = prepare_problem(case())
    result = evaluate_delivery(problem, {'a': 'x', 'b': 'x', 'c': 'y'})
    assert result['objective'] == 3
    assert [row['deliveredFiles'] for row in result['configurations']] == [['a', 'b'], ['c']]
    assert [row['implementedRequirements'] for row in result['configurations']] == [['ra', 'rb'], ['rc']]
    assert evaluate_delivery(problem, {'a': 'x', 'b': 'x', 'c': 'x'})['objective'] == 6
    oracle = exact_partition_oracle(problem)
    assert oracle['optimal'] and oracle['best']['objective'] == 3
    assert oracle['partitionsEvaluated'] == 4


def test_module_induced_dependency_is_closed_until_fixed_point():
    """Дополнительный b из модуля a тянет c, хотя a напрямую не зависит от c."""
    data = case()
    data['dependencies'] = [['b', 'c']]
    data['configurations'] = {'a-only': ['ra']}
    result = evaluate_delivery(data, {'a': 'x', 'b': 'x', 'c': 'y'})
    assert result['objective'] == 3
    assert result['configurations'][0]['deliveredFiles'] == ['a', 'b', 'c']
    assert result['closurePolicy'] == 'module-and-dependency-fixed-point'


def test_explicit_dependency_direction_is_not_reversed():
    """A depends on B: требование B не тянет A при раздельных модулях."""
    data = case()
    data['configurations'] = {'a-only': ['ra'], 'b-only': ['rb']}
    data['moduleLimit'] = 3
    result = evaluate_delivery(data, {file: file for file in data['files']})
    assert [row['deliveredFiles'] for row in result['configurations']] == [['a', 'b'], ['b']]
    components = dependency_components(data)
    assert components.index(('b',)) < components.index(('a',))
    assert result['direction'] == 'dependent-to-prerequisite'


def test_implementation_requires_all_mapped_files():
    """Попадание только части multi-file requirement не считается реализацией."""
    data = case()
    data['dependencies'] = []
    data['requirements']['both'] = ['a', 'b']
    data['configurations'] = {'a-only': ['ra']}
    data['moduleLimit'] = 3
    row = evaluate_delivery(data, {file: file for file in data['files']})['configurations'][0]
    assert row['implementedRequirements'] == ['ra']


def test_cycle_scc_is_indivisible_except_labelled_ablation():
    """Обычный API отклоняет split SCC, file ablation явно отмечает ослабление."""
    data = case()
    data['dependencies'].append(['b', 'a'])
    assert ('a', 'b') in dependency_components(data)
    with pytest.raises(DeliveryAnalysisError, match='SCC'):
        evaluate_delivery(data, {'a': 'x', 'b': 'y', 'c': 'x'})
    result = evaluate_delivery(data, {'a': 'x', 'b': 'y', 'c': 'x'}, require_scc=False)
    assert not result['assignmentSccConsistent']
    assert result['feasibilityScope'] == 'raw-file-ablation-SCC-constraint-relaxed'
    main = exact_partition_oracle(data)
    assert main['best']['assignment']['a'] == main['best']['assignment']['b']


def test_evaluator_and_enumerator_match_independent_bitset_oracle():
    """Все label assignments четырёх файлов проверены независимым алгоритмом."""
    data = case()
    data['files'].append('d')
    data['dependencies'] = [['a', 'b'], ['c', 'd'], ['d', 'c']]
    data['requirements']['mixed'] = ['a', 'd']
    data['configurations']['mixed'] = ['mixed']
    expected_min = None
    for values in product(('x', 'y'), repeat=4):
        labels = dict(zip(sorted(data['files']), values))
        score, rows = bitset_oracle(data, labels)
        actual = evaluate_delivery(data, labels, require_scc=False)
        assert actual['objective'] == score
        for row in actual['configurations']:
            assert row['deliveredFiles'] == rows[row['id']]['files']
            assert row['implementedRequirements'] == rows[row['id']]['requirements']
        expected_min = score if expected_min is None else min(expected_min, score)
    assert exact_partition_oracle(data, condense=False)['best']['objective'] == expected_min


def test_empty_configuration_delivers_nothing():
    """Неиспользуемая комплектация допустима; пустые mappings требований — нет."""
    data = case()
    data['configurations']['empty'] = []
    row = next(row for row in evaluate_delivery(data, {'a': 'x', 'b': 'x', 'c': 'y'})['configurations'] if row['id'] == 'empty')
    assert row['deliveredFiles'] == row['implementedRequirements'] == []
    assert row['implementedCount'] == 0


def test_prepare_is_immutable_roundtrippable_and_order_independent():
    """Изменение caller dict/as_dict не меняет уже проверенный источник."""
    data = case()
    original = deepcopy(data)
    prepared = prepare_problem(data)
    assert data == original
    data['files'].reverse()
    data['dependencies'].reverse()
    assert prepare_problem(data).problem_hash == prepared.problem_hash
    copy = prepared.as_dict()
    assert prepare_problem(copy) == prepared
    copy['files'].clear()
    assert prepared.files == ('a', 'b', 'c')
    with pytest.raises(FrozenInstanceError):
        prepared.module_limit = 1


@pytest.mark.parametrize('change', [
    {'files': []}, {'files': ['a', 'a']}, {'requirements': {}}, {'configurations': {}},
    {'requirements': {'ra': []}}, {'requirements': {'ra': ['missing']}},
    {'configurations': {'bad': ['missing']}}, {'dependencies': [['a', 'missing']]},
    {'dependencies': [['a', 'a']]}, {'dependencies': [{'source': 'a', 'target': 'b'}]},
    {'moduleLimit': True}, {'moduleLimit': 0}, {'moduleLimit': 9},
    {'files': [' ']}, {'source': 'real-production'}, {'direction': 'prerequisite-to-dependent'},
    {'unexpected': 1}, {'schemaVersion': 'future'},
])
def test_invalid_model_fails_before_search(change):
    """Malformed/unknown/out-of-budget inputs не становятся пустым успешным решением."""
    data = case()
    data.update(change)
    with pytest.raises(DeliveryAnalysisError):
        prepare_problem(data)


@pytest.mark.parametrize('labels', [{'a': 'x'}, {'a': 'x', 'b': 'y', 'c': 'z'}, {'a': 'x', 'b': None, 'c': 'x'}])
def test_assignment_is_full_and_module_bounded(labels):
    """Назначение не может пропустить требуемые файлы или превысить число модулей."""
    with pytest.raises(DeliveryAnalysisError):
        evaluate_delivery(case(), labels)


def test_search_is_seeded_bounded_and_keeps_fully_evaluated_best():
    """Каждый оператор считает промежуточные evaluations и не обещает оптимум."""
    for condense in (False, True):
        for operator in ('uniform', 'tc-inspired'):
            first = genetic_search(case(), condense=condense, operator=operator, seed=7, evaluation_budget=40)
            assert first == genetic_search(case(), condense=condense, operator=operator, seed=7, evaluation_budget=40)
            assert first['evaluationsUsed'] <= 40
            assert first['best']['objective'] >= exact_partition_oracle(case(), condense=condense)['best']['objective']
            assert first['optimal'] is False
            assert first['best']['objective'] <= min(value['objective'] for value in (
                evaluate_delivery(case(), labels) for labels in baseline_assignments(case()).values()))
            assert all(a >= b for a, b in zip(first['history'], first['history'][1:]))


def test_factorial_comparison_exposes_encoding_and_operator_differences():
    """Все четыре клетки, gap и ограничения сохраняются без объявления победителя."""
    report = compare_search(case(), evaluation_budget=40)
    assert {(row['condensed'], row['operator']) for row in report['outcomes']} == {
        (False, 'uniform'), (False, 'tc-inspired'), (True, 'uniform'), (True, 'tc-inspired')}
    assert report['paperReproduction'] is False
    assert report['syntheticOnly'] is True
    assert all(row['evaluationsUsed'] == 40 for row in report['outcomes'])
    assert all(row['gapToExact'] >= 0 for row in report['outcomes'])
    assert all('not isolate a pure causal effect' in row['limitations'][0] for row in report['outcomes'])


def test_exact_oracle_size_limit_is_explicit():
    """Девять независимых компонентов не запускают необозначенный exponential search."""
    data = case()
    data['files'] = [str(index) for index in range(9)]
    data['dependencies'] = []
    data['requirements'] = {'one': ['0']}
    data['configurations'] = {'one': ['one']}
    with pytest.raises(DeliveryAnalysisError, match='8 components'):
        exact_partition_oracle(data)
    report = compare_search(data, evaluation_budget=8)
    assert all(row['oracle']['status'] == 'unavailable' and row['gapToExact'] is None for row in report['outcomes'])


@pytest.mark.parametrize('kwargs', [{'evaluation_budget': True}, {'population': 1}, {'generations': 0}, {'seed': -1}])
def test_search_parameters_rejected_before_expensive_oracle(kwargs, monkeypatch):
    """Некорректный budget отвергается до какого-либо oracle/evaluator вызова."""
    import atlas.delivery_analysis.search as search
    def must_not_run(*args, **kw):
        """Отмечает недопустимый преждевременный старт oracle."""
        raise AssertionError('oracle ran')
    monkeypatch.setattr(search, 'exact_partition_oracle', must_not_run)
    with pytest.raises(DeliveryAnalysisError):
        compare_search(case(), **kwargs)


@pytest.mark.parametrize('kwargs', [{'operator': []}, {'operator': 'TC-LM'}, {'condense': 1}])
def test_unknown_algorithm_and_nonboolean_encoding_are_explicit(kwargs):
    """Неверный вызов не маскируется точным авторским именем или truthy значением."""
    with pytest.raises(DeliveryAnalysisError):
        genetic_search(case(), **kwargs)


def test_frozen_report_retains_all_cells_and_lost_cases():
    """Сохранённые результаты не отфильтрованы по удачным случаям или операторам."""
    from pathlib import Path
    import json
    path = Path(__file__).resolve().parents[1] / 'tools/performance/delivery-analysis/report-20261030.json'
    report = json.loads(path.read_text())
    assert len(report['cases']) == 4
    for case_row in report['cases']:
        fixture = path.parent / 'fixtures' / case_row['fixture']
        assert prepare_problem(json.loads(fixture.read_text())).problem_hash == case_row['problemHash']
        assert prepare_problem(case_row['problem']).problem_hash == case_row['problemHash']
    cells = [row for case_row in report['cases'] for run in case_row['runs'] for row in run['outcomes']]
    assert len(cells) == 48
    assert all(row['evaluationsUsed'] == row['evaluationLimit'] == 64 for row in cells)
    assert len(report['lostCases']) == sum(row['gapToExact'] > 0 for row in cells)
    assert report['lostCases'] and not report['paperReproduction']
