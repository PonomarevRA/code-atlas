"""Проверяемые инварианты class/folder анализа, а не копии его реализации."""

from copy import deepcopy

import networkx as nx
import pytest

from atlas.module_analysis import (ModuleAnalysisError, assess_partition,
                                   compare_partitions, project_modules)
from atlas.module_analysis.sarf import dedication_graph, sarf_partition


def fixture():
    """Два явных класса и функция без класса на трёх границах папок."""
    return {'analysisStatus': 'complete', 'snapshotId': 'frozen-1', 'nodes': [
        {'id': 'a', 'kind': 'class', 'file': 'src/orders/a.cs', 'classIdentity': 'App.Orders'},
        {'id': 'm', 'kind': 'method', 'file': 'src/orders/a.cs', 'memberFacts': {'ownerNodeId': 'a'}},
        {'id': 'b', 'kind': 'class', 'file': 'src/shared/b.cs'},
        {'id': 'n', 'kind': 'method', 'file': 'src/shared/b.cs', 'memberFacts': {'ownerNodeId': 'b'}},
        {'id': 'f', 'kind': 'function', 'file': 'tools/run.py'}], 'edges': [
        {'source': 'a', 'target': 'm', 'relation': 'contains'},
        {'source': 'b', 'target': 'n', 'relation': 'defines'},
        {'source': 'm', 'target': 'n', 'relation': 'calls'},
        {'source': 'n', 'target': 'm', 'relation': 'references'}]}


def test_folder_projection_preserves_direction_raw_ids_and_input():
    """Containment не становится зависимостью; обратное направление не исчезает."""
    source = fixture()
    original = deepcopy(source)
    projected = project_modules(source)
    assert source == original
    assert projected['counts']['units'] == 3
    assert projected['coverage']['status'] == 'complete'
    ownership = projected['ownership']
    assert ownership['a'] == ownership['m'] != ownership['b'] == ownership['n']
    assert {(edge['source'], edge['target'], edge['relation']) for edge in projected['edges']} == {
        (ownership['a'], ownership['b'], 'calls'), (ownership['b'], ownership['a'], 'references')}
    assessment = assess_partition(projected)
    assert assessment['metrics']['observedCycleComponents'] == [sorted([ownership['a'], ownership['b']])]


def test_node_edge_order_does_not_change_projection_or_hash():
    """Одинаковые факты имеют одинаковую идентичность при перестановке входа."""
    source = fixture()
    expected = project_modules(source)
    source['nodes'].reverse()
    source['edges'].reverse()
    assert project_modules(source) == expected


def test_partial_class_identity_merges_files_without_label_guess():
    """Одна explicit type identity объединяет partial; одинаковые labels не объединяют типы."""
    source = {'analysisStatus': 'complete', 'nodes': [
        {'id': 'p1', 'kind': 'class', 'file': 'x/c.cs', 'classIdentity': 'App.N.C', 'label': 'C'},
        {'id': 'p2', 'kind': 'class', 'file': 'y/c.cs', 'classIdentity': 'App.N.C', 'label': 'C'},
        {'id': 'm', 'kind': 'method', 'file': 'y/c.cs', 'memberFacts': {'ownerNodeId': 'p2'}},
        {'id': 'other', 'kind': 'class', 'file': 'z/c.cs', 'label': 'C'}], 'edges': []}
    projected = project_modules(source, level='class')
    assert len(projected['units']) == 2
    owners = projected['ownership']
    assert owners['p1'] == owners['p2'] == owners['m'] != owners['other']
    shared = next(unit for unit in projected['units'] if unit['id'] == owners['m'])
    assert shared['files'] == ['x/c.cs', 'y/c.cs']


def test_caller_class_identity_is_propagated_and_labelled():
    """Переданная агентом mapping не выдаётся за подтверждённый source binding."""
    projected = project_modules(fixture(), level='class', ownership={'a': 'caller:orders'})
    assert projected['ownership']['a'] == projected['ownership']['m']
    unit = next(unit for unit in projected['units'] if unit['label'] == 'caller:orders')
    assert unit['ownershipSources'] == ['caller-supplied']
    assert projected['scope']['ownershipSource'] == 'caller-supplied'


def test_nested_class_owns_its_members_without_parent_overlap():
    """Вложенный тип — самостоятельная единица; методы принадлежат ближайшему типу."""
    source = {'analysisStatus': 'complete', 'nodes': [
        {'id': 'outer', 'kind': 'class', 'file': 'x.cs'},
        {'id': 'inner', 'kind': 'class', 'file': 'x.cs'},
        {'id': 'method', 'kind': 'method', 'file': 'x.cs'}], 'edges': [
        {'source': 'outer', 'target': 'inner', 'relation': 'contains'},
        {'source': 'inner', 'target': 'method', 'relation': 'contains'}]}
    projected = project_modules(source, level='class')
    assert projected['ownership']['outer'] != projected['ownership']['inner'] == projected['ownership']['method']
    assert sum(len(unit['sourceIds']) for unit in projected['units']) == 3


def test_ambiguous_owner_and_top_level_function_are_unknown():
    """Неоднозначность не решается выбором первого класса или названием функции."""
    source = fixture()
    source['edges'].append({'source': 'a', 'target': 'f', 'relation': 'contains'})
    source['edges'].append({'source': 'b', 'target': 'f', 'relation': 'contains'})
    projected = project_modules(source, level='class')
    assert 'f' not in projected['ownership']
    assert projected['unknownOwnership'] == [{'rawId': 'f', 'reason': 'ambiguous-class-owner'}]
    assert projected['coverage']['status'] == 'partial'
    assert assess_partition(projected)['metrics']['absenceOfCycles'] is None


@pytest.mark.parametrize('folders', [['src', 'src/orders'], ['.', 'tools'], ['src', 'src'], ['../outside']])
def test_folder_frontier_rejects_overlap_and_escape(folders):
    """Непересекающийся frontier не удваивает raw nodes и не выходит за проект."""
    with pytest.raises(ModuleAnalysisError):
        project_modules(fixture(), folders=folders)


def test_selected_scope_counts_only_relevant_boundary():
    """Связи двух посторонних единиц не увеличивают coupling выбранной папки."""
    source = fixture()
    source['nodes'].append({'id': 'g', 'file': 'elsewhere/g.py'})
    source['edges'].append({'source': 'f', 'target': 'g', 'relation': 'calls'})
    projected = project_modules(source, folders=['src/orders'])
    assert set(projected['ownership']) == {'a', 'm'}
    assert projected['counts']['externalDependencies'] == 2
    assert projected['edges'] == []


@pytest.mark.parametrize('patch', [
    {'confidence': 'inferred'}, {'resolution': 'ambiguous'},
    {'sourceHash': 'stale'}, {'evidence': {'sourceHash': 'stale'}}])
def test_uncertain_or_stale_edge_cannot_prove_dependency(patch):
    """Существующий source-hash/confidence gate применяется к проекции."""
    source = fixture()
    source['nodes'][1]['complexity'] = {'sourceHash': 'current'}
    source['edges'][2].update(patch)
    projected = project_modules(source)
    assert projected['coverage']['uncertainDependencyCount'] == 1
    assert len(projected['edges']) == 1
    assert assess_partition(projected)['metrics']['absenceOfCycles'] is None


def test_missing_endpoint_and_missing_analysis_status_remain_partial():
    """Отсутствие подтверждения полноты не заменяется успешным статусом регистрации."""
    source = fixture()
    del source['analysisStatus']
    source['edges'].append({'source': 'm', 'target': 'missing', 'relation': 'calls'})
    projected = project_modules(source)
    assert projected['coverage']['sourceAnalysisStatus'] == 'unknown'
    assert projected['coverage']['missingEndpointCount'] == 1
    assert projected['coverage']['status'] == 'partial'


def test_duplicate_raw_fact_is_not_counted_twice():
    """Точная копия ребра — один факт; разные call-sites остаются разными фактами."""
    source = fixture()
    source['edges'].append(deepcopy(source['edges'][2]))
    source['edges'].append({**source['edges'][2], 'line': 8})
    projected = project_modules(source)
    assert projected['counts']['dependencyFacts'] == 3


def test_within_unit_dependencies_remain_visible_in_assessment():
    """Группировка не стирает внутренние вызовы класса/папки из наблюдаемых счётчиков."""
    source = fixture()
    source['edges'].append({'source': 'a', 'target': 'm', 'relation': 'calls'})
    projected = project_modules(source)
    metrics = assess_partition(projected)['metrics']
    assert metrics['withinUnitDependencyWeight'] == 1
    assert metrics['internalDependencyWeight'] + metrics['externalDependencyWeight'] == projected['counts']['dependencyFacts']


@pytest.mark.parametrize('bad', [[], [[]], [['missing']], [['placeholder'], ['placeholder']]])
def test_partition_requires_exact_cover(bad):
    """Нельзя улучшить метрику, выбросив неудобную единицу из оценки."""
    with pytest.raises(ModuleAnalysisError):
        assess_partition(project_modules(fixture()), bad)


def test_dedication_and_directed_modularity_match_hand_oracle():
    """Независимый расчёт III.A: fanin(x)=2, fanin(y)=1 и Q=.25."""
    graph = nx.DiGraph([('a', 'x'), ('b', 'x'), ('a', 'y')])
    weighted = dedication_graph(graph)
    assert weighted['a']['x']['weight'] == weighted['b']['x']['weight'] == .5
    assert weighted['a']['y']['weight'] == 1
    assert nx.community.modularity(weighted, [{'a', 'y'}, {'b', 'x'}]) == pytest.approx(.25)
    partition = sarf_partition(graph)
    assert nx.community.modularity(weighted, partition) == pytest.approx(.25)


def test_sarf_reciprocal_and_isolates_preserved():
    """Отрицательный singleton Q допускает слияние; изолят не теряется."""
    graph = nx.DiGraph([('a', 'b'), ('b', 'a')])
    graph.add_node('isolated')
    assert sorted(map(sorted, sarf_partition(graph))) == [['a', 'b'], ['isolated']]


def test_leiden_unavailable_is_not_silently_replaced(monkeypatch):
    """Недоступность backend видна отдельно от успешного Louvain."""
    def unavailable(*args, **kwargs):
        """Эмулирует отсутствие optional backend без вызова другого алгоритма."""
        raise ImportError('Leiden backend absent')
    monkeypatch.setattr(nx.community, 'leiden_communities', unavailable)
    result = compare_partitions(project_modules(fixture()), algorithms=['louvain', 'leiden'])
    assert result['proposals'][0]['status'] == 'available'
    assert result['proposals'][1] == {'algorithm': 'leiden', 'status': 'unavailable', 'reason': 'Leiden backend absent',
                                     'elapsedSeconds': result['proposals'][1]['elapsedSeconds']}
    assert result['defaultChanged'] is False


def test_sarf_simple_variant_and_objective_limitations_are_explicit():
    """Простой directed вариант не маскируется под повторение эксперимента статьи."""
    result = compare_partitions(project_modules(fixture()), algorithms=['sarf-simple-directed'])
    item = result['proposals'][0]
    assert item['status'] == 'available' and item['variant'] == 'simple-directed-greedy'
    assert any('member-level' in warning for warning in item['limitations'])
    assert item['preprocessing'] == 'directed-simple-dedication'


def test_scoped_large_graph_is_allowed_but_full_graph_budget_is_explicit():
    """Большой repository можно исследовать по папке без полного class clustering."""
    source = {'analysisStatus': 'complete', 'nodes': [
        {'id': str(index), 'file': f'other/{index}.py'} for index in range(10_001)], 'edges': []}
    source['nodes'].append({'id': 'chosen', 'file': 'chosen/a.py'})
    with pytest.raises(ModuleAnalysisError) as caught:
        project_modules(source)
    assert caught.value.status == 413 and caught.value.code == 'module_analysis_budget'
    selected = project_modules(source, folders=['chosen'])
    assert set(selected['ownership']) == {'chosen'}
    assert selected['outsideScopeCount'] == 10_001 and selected['outsideScopeIdsTruncated']


def test_empty_graph_has_unknown_objective_without_division_by_zero():
    """Пустой граф не получает выдуманную максимальную оценку качества."""
    projected = project_modules({'analysisStatus': 'complete', 'nodes': [], 'edges': []})
    assessment = assess_partition(projected)
    assert assessment['metrics']['directedModularity'] is None
    assert assessment['groups'] == []
    assert compare_partitions(projected)['proposals'][0]['partition'] == []


def test_sarf_cubic_budget_is_explicit_for_nonempty_large_graph():
    """Бюджет simple greedy не допускает дорогой незаметный запуск на всей карте."""
    source = {'analysisStatus': 'complete', 'nodes': [
        {'id': str(index), 'file': f'unit{index}/a.py'} for index in range(201)],
        'edges': [{'source': str(index), 'target': str(index + 1), 'relation': 'calls'} for index in range(200)]}
    result = compare_partitions(project_modules(source), algorithms=['sarf-simple-directed'])
    assert result['proposals'][0]['status'] == 'unavailable'
    assert '200 units' in result['proposals'][0]['reason']


def test_large_class_map_budget_advice_uses_available_folder_mode():
    """Class mode не предлагает невозможный для него параметр folders."""
    source = {'nodes': [{'id': str(index)} for index in range(10_001)], 'edges': []}
    with pytest.raises(ModuleAnalysisError) as caught:
        project_modules(source, level='class')
    assert caught.value.status == 413
    assert 'Class analysis is a bounded pilot' in str(caught.value)
    assert 'use folder level' in str(caught.value)


def test_partial_directed_q_is_not_a_lower_bound():
    """Независимый двухузловой контрпример: наблюдаемые Q0 и полные Q−.5."""
    source = {'analysisStatus': 'partial', 'nodes': [
        {'id': 'a', 'file': 'left/a.py'}, {'id': 'b', 'file': 'right/b.py'}],
        'edges': [{'source': 'a', 'target': 'b', 'relation': 'calls'}]}
    observed = assess_partition(project_modules(source))
    source['analysisStatus'] = 'complete'
    source['edges'].append({'source': 'b', 'target': 'a', 'relation': 'calls'})
    complete = assess_partition(project_modules(source))
    # Hand formula: singleton internal=0. One-way kout*kin=0; reciprocal
    # expected term=(1*1+1*1)/2²=.5, hence Q=−.5.
    assert observed['metrics']['directedModularity'] == pytest.approx(0)
    assert complete['metrics']['directedModularity'] == pytest.approx(-.5)
    assert observed['metrics']['externalDependencyWeight'] == 1
    assert complete['metrics']['externalDependencyWeight'] == 2
    assert 'not bounds under partial coverage' in observed['interpretation']
