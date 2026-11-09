"""Рекомендации читаемости не меняют исходники, ветви и Graphify-снимок."""

from copy import deepcopy

import pytest

from atlas.execution_path import build_execution_path
from atlas.node_description import build_node_description


def source_project(tmp_path, body):
    """Создаёт один выбранный метод и соседний метод, исключённый из анализа."""
    source = tmp_path / 'Handler.cs'
    text = f'class Handler {{ object Run(bool a, bool b) {{ {body} }} object Neighbor() {{ return a ? b ? A() : B() : C(); }} }}'
    source.write_text(text)
    graph = {'nodes': [{'id':'run','label':'Run','kind':'method','file':'Handler.cs'}], 'edges':[]}
    return source, graph


@pytest.mark.parametrize('expression', ['a ? (b ? A() : B()) : C()', 'new { X = a ? 1 : 0, Y = b ? 2 : 3 }',
                                      'a ? b switch { true => A(), _ => B() } : C()'])
def test_complex_return_recommendation_is_shared_and_readonly(tmp_path, expression):
    """Вложенные/независимые выборы дают мягкую рекомендацию, одинаковую в двух API."""
    source, graph = source_project(tmp_path, f'return {expression};')
    before = source.read_bytes(), deepcopy(graph)
    description = build_node_description(tmp_path, graph, 'run', 's')
    facts = description['readabilityRecommendations']
    recommendation, = facts['items']
    assert facts['status'] == 'parsed' and not facts['truncated']
    assert recommendation['kind'] == 'extract-return-result'
    assert recommendation['severity'] == 'recommendation'
    assert recommendation['reasons'] == ['multiple-value-branches']
    assert recommendation['expressionPreview'] == expression
    assert recommendation['line'] == recommendation['endLine'] == 1
    assert description['sourceHash'] and description['file'] == 'Handler.cs'
    path = build_execution_path(tmp_path, graph, 'run', 's')
    assert path['steps'][0]['readabilityRecommendations'] == facts
    assert (source.read_bytes(), graph) == before


@pytest.mark.parametrize('body', ['return null;', 'return a;', 'return a ? A() : B();',
                                'return new Func<object>(() => { return a ? b ? A() : B() : C(); });',
                                'object Inner() { return a ? b ? A() : B() : C(); } return a;',
                                'return ref (a ? ref (b ? ref x : ref y) : ref z);',
                                'return;'])
def test_simple_returns_and_nested_scopes_are_not_flagged(tmp_path, body):
    """Не навязывает result простым выходам и не относит чужой return к caller."""
    _, graph = source_project(tmp_path, body)
    facts = build_node_description(tmp_path, graph, 'run', 's')['readabilityRecommendations']
    assert facts['items'] == [] and not facts['truncated']


def test_long_return_preview_is_bounded_and_recommendation_has_stable_id(tmp_path):
    """Порог соответствует усечению текста, но рекомендация сохраняет точную длину и ID."""
    _, graph = source_project(tmp_path, 'return "' + 'д' * 1200 + '";')
    facts = build_node_description(tmp_path, graph, 'run', 's')['readabilityRecommendations']
    row, = facts['items']
    assert row['reasons'] == ['long-return-expression']
    assert row['expressionChars'] == 1202 and len(row['expressionPreview']) == 160
    assert row['previewTruncated'] is True
    assert build_node_description(tmp_path, graph, 'run', 's')['readabilityRecommendations'] == facts


def test_extracting_result_removes_advice_and_preserves_lexical_controls(tmp_path):
    """Проверяет рекомендуемую форму на AST, не заявляя семантическую эквивалентность произвольного кода."""
    expression = 'a ? (b ? A() : B()) : C()'
    source, graph = source_project(tmp_path, f'return {expression};')
    before = build_node_description(tmp_path, graph, 'run', 's')
    source.write_text(source.read_text().replace(f'return {expression};', f'object result = {expression}; return result;', 1))
    after = build_node_description(tmp_path, graph, 'run', 's')
    assert before['readabilityRecommendations']['items']
    assert after['readabilityRecommendations']['items'] == []
    assert [branch['condition'] for branch in after['controlFlow']['branches']] == [branch['condition'] for branch in before['controlFlow']['branches']]
    assert [event['callee'] for event in after['events']] == [event['callee'] for event in before['events']]
    assert after['controlFlow']['terminals'][0]['text'] == 'return result;'


def test_recommendation_and_ast_budgets_are_explicit(tmp_path, monkeypatch):
    """Ограничения не становятся ошибкой анализа и не дают неограниченного MCP-ответа."""
    from atlas import source_readability
    _, graph = source_project(tmp_path, 'return a ? b ? A() : B() : C();' * 21)
    facts = build_node_description(tmp_path, graph, 'run', 's')['readabilityRecommendations']
    assert len(facts['items']) == 20 and facts['truncated']
    monkeypatch.setattr(source_readability, 'MAX_AST_NODES', 5)
    facts = build_node_description(tmp_path, graph, 'run', 's')['readabilityRecommendations']
    assert facts['truncated'] and facts['status'] == 'parsed'


def test_missing_source_has_unavailable_recommendations(tmp_path):
    """Пустой ответ без доступного исходника не является успешной проверкой читаемости."""
    _, graph = source_project(tmp_path, 'return a;')
    graph['nodes'][0]['file'] = 'Missing.cs'
    facts = build_node_description(tmp_path, graph, 'run', 's')['readabilityRecommendations']
    assert facts['status'] == 'unavailable' and facts['items'] == []
