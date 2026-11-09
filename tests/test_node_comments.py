"""Пояснения исходника доступны сразу в обоих MCP, с честным владельцем и ревизией."""

import asyncio
from copy import deepcopy

import pytest

from atlas.attribute_graph import enrich_attribute_graph
from atlas.agent_views import search_nodes
from atlas.graph_context import read_graph_node, read_graph_neighbors
from atlas.mcp_graph import build_mcp_graph
from atlas.mcp_bridge import describe_node
from atlas.node_comments import explanatory_comments
from atlas.store import write_json
from test_attribute_graph import project


def test_comments_capture_once_and_survive_working_tree_changes(tmp_path, monkeypatch):
    """Узел, поиск и соседи читают одно захваченное пояснение, без повторного source I/O."""
    from atlas import attribute_graph
    source = '''/// <summary>Считает расходы по операциям.</summary>
class Calculator {
 /// <summary>Возвращает итог расходов.</summary>
 void Total() { // Внутренняя деталь, не назначение метода.
 }
 void Apply() {}
}'''
    graph, _ = project(tmp_path, source)
    before = deepcopy(graph)
    reads = []
    original = attribute_graph._read_regular_file

    def counted(root, path, *args):
        """Считает фактическое чтение исходного C# в уже существующем capture-проходе."""
        reads.append(str(path))
        return original(root, path, *args)

    monkeypatch.setattr(attribute_graph, '_read_regular_file', counted)
    captured = enrich_attribute_graph(graph, tmp_path)
    captured['metadata']['snapshotId'] = 'captured'
    assert reads == ['Types.cs'] and graph == before
    nodes = {node['label']: node for node in captured['nodes']}
    total, apply = nodes['Total'], nodes['Apply']
    assert total['comment'] == 'Возвращает итог расходов.'
    assert total['implementationComment'] == 'Считает расходы по операциям.'
    assert total['implementationName'] == 'Calculator'
    assert total['commentScope'] == 'declaration'
    assert apply['comment'] == 'Считает расходы по операциям.'
    assert apply['commentScope'] == 'containing-type'
    assert apply['commentSource']['line'] == 2
    assert total['commentSource']['sourceKind'] == 'snapshot-captured-source'
    assert len(total['commentSource']['sourceHash']) == 64
    (tmp_path / 'Types.cs').write_text('class Calculator {}')
    assert search_nodes(captured, 'Total')['nodes'][0]['comment'] == total['comment']
    assert read_graph_node(captured, total['id'])['node']['comment'] == total['comment']
    neighbors = read_graph_neighbors(captured, nodes['Calculator']['id'])
    assert next(node for node in neighbors['nodes'] if node['id'] == total['id'])['comment'] == total['comment']
    assert reads == ['Types.cs']
    path = tmp_path / 'mcp.json'
    compatible = build_mcp_graph(captured)
    write_json(path, compatible)
    response = asyncio.run(describe_node(path, total['id']))
    import json
    attributes_line = next(line for line in response['text'].splitlines() if 'Attributes:' in line)
    # Graphify caps its text at 1000 characters; the full exported fields stay exact.
    attributes = next(node['attributes'] for node in compatible['nodes'] if node['id'] == total['id'])
    assert attributes['atlas.comment'] == 'Возвращает итог расходов.'
    assert attributes['atlas.implementationComment'] == 'Считает расходы по операциям.'
    import re
    compact = re.search(r'"atlas\.2\.comment": (\{[^{}]*\})', attributes_line)
    assert compact is not None
    assert json.loads(compact.group(1))['text'] == total['comment'][:24]


@pytest.mark.parametrize('text, expected', [
    ('// Значение < порога.\n', 'Значение < порога.'),
    ('/* Подбирает\n * подходящие операции. */\n', 'Подбирает подходящие операции.'),
    ('/// Обычное пояснение.\n', 'Обычное пояснение.'),
    ('/// <summary>Незавершённый XML\n', None),
    ('/// <!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]>\n', None),
    ('', None),
])
def test_plain_comments_and_unsupported_xml_are_distinct(tmp_path, text, expected):
    """Прилегающий комментарий очищается от маркеров; malformed XML и отсутствие не выдумывают описание."""
    graph, _ = project(tmp_path, 'class C {\n' + text + ' void Run() {}\n}')
    result = enrich_attribute_graph(graph, tmp_path)
    method = next(node for node in result['nodes'] if node['label'] == 'Run')
    assert method.get('comment') == expected


def test_nested_type_context_and_refresh_remove_old_comments(tmp_path):
    """Метод принадлежит ближайшему типу; повторный capture удаляет старые собственные поля."""
    graph, _ = project(tmp_path, '''// Внешний тип.
class Outer {
 // Вложенная реализация.
 class Inner {
  void Run() {}
 }
}''')
    captured = enrich_attribute_graph(graph, tmp_path)
    method = next(node for node in captured['nodes'] if node['label'] == 'Run')
    inner = next(node for node in captured['nodes'] if node['label'] == 'Inner')
    assert method['comment'] == 'Вложенная реализация.'
    assert method['implementationName'] == inner['implementationName'] == 'Inner'
    (tmp_path / 'Types.cs').unlink()
    refreshed = enrich_attribute_graph(captured, tmp_path)
    assert all('comment' not in node and 'commentSource' not in node for node in refreshed['nodes'])


def test_comment_limits_and_legacy_nodes_are_explicit():
    """Предел пояснения отмечен, старые снимки остаются совместимыми и не читают рабочие файлы."""
    fields = explanatory_comments({'summary': 'а' * 2500}, None, source_kind='working-tree',
                                  source_hash='hash', filename='C.cs', line=3)
    assert len(fields['comment']) == 2000
    assert fields['commentSource']['truncated']
    graph = {'nodes': [{'id': 'old', 'label': 'Legacy'}], 'edges': [], 'metadata': {}}
    assert 'comment' not in search_nodes(graph, 'Legacy')['nodes'][0]
    assert 'comment' not in read_graph_node(graph, 'old')['node']


def test_ambiguous_overloads_do_not_gain_guessed_comments(tmp_path):
    """Совпавший raw-ID нескольких объявлений не получает чужое пояснение."""
    graph, _ = project(tmp_path, 'class C { /* First */ void Go() {} /* Second */ void Go(int x) {} }')
    methods = [node for node in graph['nodes'] if node['kind'] == 'method']
    graph['nodes'].remove(methods[1])
    graph['edges'] = [edge for edge in graph['edges'] if edge['target'] != methods[1]['id']]
    captured = enrich_attribute_graph(graph, tmp_path)
    assert 'comment' not in next(node for node in captured['nodes'] if node['id'] == methods[0]['id'])
