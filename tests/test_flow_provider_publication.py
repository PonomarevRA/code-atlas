"""Actual member publication reaches common providers without changing upstream identity or field facts."""
from copy import deepcopy
import pytest
from atlas.member_graph import enrich_member_graph
from atlas.node_description import build_node_description


@pytest.mark.parametrize('suffix,text', [
    ('.js','const run = (input) => { if (input) return work(input); return null; };'),
    ('.ts','const run = (input: boolean) => { if (input) return work(input); return null; };'),
    ('.tsx','const run = (input: boolean) => { if (input) return work(input); return null; };'),
])
def test_function_valued_field_keeps_raw_id_and_gets_real_method_body(tmp_path, suffix, text):
    """Field storage and callable semantics coexist on one original source ID."""
    filename = 'Code' + suffix
    (tmp_path / filename).write_text(text)
    graph = {'nodes':[{'id':'file','kind':'file','label':filename,'file':filename,'line':1},
                      {'id':'raw-run','kind':'symbol','label':'run','file':filename,'line':1}], 'edges':[]}
    before = deepcopy(graph)
    captured = enrich_member_graph(graph, tmp_path)
    node = next(node for node in captured['nodes'] if node['id'] == 'raw-run')
    assert node['fieldFacts'] and node['dataFacts'] and node['callableFacts']['functionValued'] and node['_callable']
    assert any(edge['source']=='file' and edge['target']=='raw-run' and edge['relation']=='method' for edge in captured['edges'])
    result = build_node_description(tmp_path, captured, 'raw-run', 's')
    assert result['sourceStatus'] == 'parsed' and result['methodFlow']['status'] == 'parsed', result['unresolved']
    assert result['methodFlow']['contractVersion'] == 'atlas-method-flow-v1'
    assert [event['callee'] for event in result['events'] if event['kind']=='call'] == ['work']
    assert result['events'][0]['conditional']
    assert graph == before and (tmp_path / filename).read_text() == text


@pytest.mark.parametrize('suffix,text', [
    ('.kt','class Sample {\n    constructor(value: Int) {\n        work(value)\n    }\n}\n'),
    ('.java','record Sample(int value) { Sample { work(value); } }'),
])
def test_constructor_is_published_and_described_by_its_language_provider(tmp_path, suffix, text):
    """Keyword/compact constructors become discoverable without borrowing the containing type body."""
    filename = 'Code' + suffix
    (tmp_path / filename).write_text(text)
    graph = {'nodes':[{'id':'file','kind':'file','label':filename,'file':filename,'line':1}], 'edges':[]}
    captured = enrich_member_graph(graph, tmp_path)
    constructors = [node for node in captured['nodes'] if (node.get('memberFacts') or {}).get('kind')=='constructor']
    assert len(constructors)==1, captured['nodes']
    result = build_node_description(tmp_path, captured, constructors[0]['id'], 's')
    assert result['sourceStatus']=='parsed' and result['methodFlow']['status']=='parsed', result['unresolved']
    assert any(event['callee']=='work' for event in result['events'])
