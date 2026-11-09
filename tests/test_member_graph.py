"""Общий опубликованный снимок полей/методов независимо от visibility и рабочей копии."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from atlas.member_graph import enrich_member_graph
from atlas.interface_dispatch import enrich_interface_dispatch
from atlas.attribute_graph import enrich_attribute_graph
from atlas.graph_context import read_graph_neighbors, read_graph_node
from atlas.mcp_graph import build_mcp_graph
from atlas.agent_views import search_nodes
from tests.test_interface_dispatch import fixture


def capture(tmp_path, suffix, source):
    """Исходный Graphify file node дополняется реальными Tree-sitter декларациями; проект не исполняется."""
    filename = 'Code' + suffix
    path = tmp_path / filename; path.write_text(source)
    graph = {'nodes':[{'id':'file','label':filename,'file':filename,'kind':'file','line':1}], 'edges':[], 'metadata':{'snapshotId':'s'}}
    original = deepcopy(graph)
    result = enrich_member_graph(graph,tmp_path)
    assert graph == original and path.read_text() == source
    return result


@pytest.mark.parametrize('suffix,source,private,public,field_name', [
    ('.java','class C { private int count; public static final java.util.Map<String,Integer> CODES = java.util.Map.of("a",1,"b",2); private int read(int count){this.count++; return CODES.get("a");} public void Show(){} }','read','Show','count'),
    ('.py','class C:\n    CODES = {"a":1,"b":2}\n    def __init__(self):\n        self.count=0\n    def __read(self, count):\n        self.count += 1\n        return self.CODES["a"]\n    def Show(self):\n        pass\n','__read','Show','count'),
    ('.js','class C { #count=0; static CODES={a:1,b:2}; #read(count){this.#count++;return C.CODES.a;} Show(){} }','#read','Show','#count'),
    ('.ts','class C { private count=0; static CODES={a:1,b:2}; private read(count:number){this.count++;return C.CODES.a;} public Show(){} }','read','Show','count'),
    ('.go','package p\ntype C struct { Count int; cache string }\nvar CODES=map[string]int{"a":1,"b":2}\nfunc (c *C) read() int { c.Count++;return CODES["a"] }\nfunc (c *C) Show(){}\n','read','Show','Count'),
])
def test_fields_visibility_reads_writes_snapshot_and_mcp(tmp_path,suffix,source,private,public,field_name):
    graph = capture(tmp_path,suffix,source)
    nodes = {n.get('memberFacts',{}).get('name'):n for n in graph['nodes'] if n.get('memberFacts')}
    assert nodes[private]['memberFacts']['visibility']=='private'
    assert nodes[public]['memberFacts']['visibility']=='public'
    assert nodes[field_name]['fieldFacts']['kind']=='field'
    assert len(nodes['CODES']['dataFacts']['entries'])==2
    edges = {(e['source'],e['target'],e['relation']) for e in graph['edges']}
    assert (nodes[private]['id'],nodes[field_name]['id'],'reads') in edges
    assert (nodes[private]['id'],nodes[field_name]['id'],'writes') in edges
    assert (nodes[private]['id'],nodes['CODES']['id'],'reads') in edges
    published = build_mcp_graph(graph)
    raw = next(n for n in published['nodes'] if n['id']==nodes['CODES']['id'])
    assert raw['attributes']['atlas.dataFacts']['entries'][0]['value']=='1'
    neighbors = read_graph_neighbors(graph,nodes[private]['id'])
    assert next(n for n in neighbors['nodes'] if n['id']==nodes['CODES']['id'])['dataFacts']['totalEntries']==2
    assert search_nodes(graph,'CODES')['nodes'][0]['dataFacts']['entries']
    (tmp_path / ('Code'+suffix)).unlink()
    assert read_graph_node(graph,nodes['CODES']['id'])['node']['dataFacts']['entries'][1]['value']=='2'
    repeated = enrich_member_graph(graph,tmp_path)
    assert set(n['id'] for n in repeated['nodes'])==set(n['id'] for n in graph['nodes'])


def test_csharp_all_fields_properties_and_shadowed_data(tmp_path):
    source='''using System.Collections.Generic;
class C {
 private int count;
 public int Total {get;set;}
 private readonly Dictionary<string,int> codes=new(){ {"a",1} };
 public const string KEY="a";
 private int Read(int count) { this.count++; Total=1; return codes[KEY]; }
 public int Shadow() { var codes=new Dictionary<string,int>(); return codes["a"]; }
}'''
    graph,_=fixture(tmp_path,source)
    result=enrich_attribute_graph(enrich_interface_dispatch(graph,tmp_path),tmp_path)
    nodes={n.get('memberFacts',{}).get('name'):n for n in result['nodes'] if n.get('memberFacts')}
    assert nodes['count']['memberFacts']['visibility']=='private'
    assert nodes['Total']['memberFacts']['visibility']=='public'
    assert nodes['Read']['memberFacts']['visibility']=='private'
    assert nodes['Shadow']['memberFacts']['visibility']=='public'
    assert nodes['codes']['dataFacts']['entries'][0]['value']=='1'
    edges={(e['source'],e['target'],e['relation']) for e in result['edges']}
    assert (nodes['Read']['id'],nodes['count']['id'],'reads') in edges
    assert (nodes['Read']['id'],nodes['count']['id'],'writes') in edges
    assert (nodes['Read']['id'],nodes['Total']['id'],'writes') in edges
    assert (nodes['Shadow']['id'],nodes['codes']['id'],'reads') not in edges


def test_unknown_receiver_and_python_local_shadow_are_not_fields(tmp_path):
    g=capture(tmp_path,'.py','class C:\n    CODES={"a":1}\n    def run(self,other):\n        CODES={"b":2}\n        return other.CODES, CODES\n')
    run=next(n for n in g['nodes'] if n.get('memberFacts',{}).get('name')=='run')
    assert not any(e['source']==run['id'] and e['relation']=='reads' for e in g['edges'])


def test_declared_ownership_keeps_raw_ids_and_non_executable_data(tmp_path):
    source='class C { private int count=0; private int run(){ return count; } }'
    g=capture(tmp_path,'.java',source)
    repeat=enrich_member_graph(g,tmp_path)
    assert set(n['id'] for n in repeat['nodes'])==set(n['id'] for n in g['nodes'])
    assert len(repeat['edges'])==len(g['edges'])
    assert all(n.get('memberFacts',{}).get('sourceKind')=='snapshot-captured-source' for n in repeat['nodes'] if n.get('memberFacts'))


@pytest.mark.parametrize('suffix,source', [
    ('.cpp', 'class C { private: int count=0; int read(){count++;return count;} public: int Show(){return count;} };'),
    ('.kt', 'class C {\n private var count: Int = 0\n private fun read(): Int { count += 1; return count }\n public fun Show() {}\n}'),
    ('.php', '<?php class C { private int $count=0; private function read(){$this->count++;return $this->count;} public function Show(){} }'),
    ('.rs', 'pub struct C { count:i32 } impl C { fn read(&mut self)->i32 {self.count += 1;self.count} pub fn Show(&self){} }'),
    ('.swift', 'class C { private var count:Int=0; private func read()->Int { count += 1; return count }; public func Show(){} }'),
])
def test_additional_languages_preserve_ownership_visibility_and_access(tmp_path, suffix, source):
    """Real installed grammars verify class ownership, access labels and compound assignment."""
    graph = capture(tmp_path, suffix, source)
    nodes = {n.get('memberFacts', {}).get('name'): n for n in graph['nodes'] if n.get('memberFacts')}
    assert nodes['read']['memberFacts']['visibility'] == 'private'
    assert nodes['Show']['memberFacts']['visibility'] == 'public'
    owner = nodes['read']['memberFacts']['ownerNodeId']
    assert owner == nodes['count']['memberFacts']['ownerNodeId'] != 'file'
    relations = {e['relation'] for e in graph['edges'] if e['source'] == nodes['read']['id'] and e['target'] == nodes['count']['id']}
    assert relations == {'reads', 'writes'}
    assert not graph['metadata']['memberGraph']['truncated']


def test_initializer_budgets_keep_declarations_with_explicit_omission(tmp_path, monkeypatch):
    """Budget exhaustion preserves field identity/accesses, with no partial dictionary advertised as complete."""
    monkeypatch.setattr('atlas.member_graph.MAX_DATA_CHARS', 1)
    graph = capture(tmp_path, '.java', 'class C { int count=123; int run(){ return count; } }')
    field = next(n for n in graph['nodes'] if n.get('fieldFacts'))
    assert 'dataFacts' not in field
    assert field['fieldFacts']['truncated']
    assert graph['metadata']['memberGraph']['truncated']
    assert any(e['target'] == field['id'] and e['relation'] == 'reads' for e in graph['edges'])


def test_method_docstring_snapshot_and_deferred_accesses(tmp_path):
    """A captured method retains its documentation; deferred lambda reads are not direct execution evidence."""
    graph = capture(tmp_path, '.py', 'class C:\n    CODES={"a":1}\n    def run(self):\n        """Return a deferred lookup."""\n        return lambda: self.CODES["a"]\n')
    method = next(n for n in graph['nodes'] if n.get('memberFacts', {}).get('name') == 'run')
    assert method['callableFacts']['description'] == 'Return a deferred lookup.'
    assert not any(e['source'] == method['id'] and e['relation'] == 'reads' for e in graph['edges'])


def test_private_method_and_data_through_real_graphify_mcp(tmp_path):
    """The actual Graphify extractor and stdio tools retain IDs and print captured dictionary entries."""
    import asyncio
    from atlas.graphify_adapter import build_graph
    from atlas.mcp_bridge import describe_node
    project = tmp_path / 'project'; project.mkdir()
    (project / 'C.java').write_text('class C { private int count; private static final java.util.Map<String,Integer> CODES=java.util.Map.of("MOEX",4); private int read(){ count++; return CODES.get("MOEX"); } public void Show(){} }')
    raw = build_graph(project, tmp_path / 'raw')
    original_ids = {n['id'] for n in raw['nodes']}
    graph = enrich_member_graph(raw, project)
    assert original_ids <= {n['id'] for n in graph['nodes']}
    assert all(edge in graph['edges'] for edge in raw['edges'])
    fields = {n.get('memberFacts', {}).get('name'): n for n in graph['nodes'] if n.get('memberFacts')}
    assert fields['read']['id'] in original_ids
    path = tmp_path / 'mcp-graph.json'; path.write_text(json.dumps(build_mcp_graph(graph)))
    response = asyncio.run(describe_node(path, fields['CODES']['id']))
    assert 'MOEX' in response['text'] and 'atlas.dataFacts' in response['text']


def test_forward_go_receiver_and_local_closures(tmp_path):
    """Forward receiver types bind locally; locals inside arrow functions never become class fields."""
    graph = capture(tmp_path, '.go', 'package p\nfunc (c *C) run(){c.Count=1}\ntype C struct {Count int}\n')
    nodes = {n.get('memberFacts', {}).get('name'): n for n in graph['nodes'] if n.get('memberFacts')}
    assert nodes['run']['memberFacts']['ownerNodeId'] == nodes['Count']['memberFacts']['ownerNodeId']
    assert {e['relation'] for e in graph['edges'] if e['source'] == nodes['run']['id']} == {'writes'}
    graph = capture(tmp_path, '.js', 'const fn=()=>{ const hidden=1; return hidden; }; class C { run(){ function local(){ const hidden=2; return hidden; } return local(); } }')
    names = [n.get('memberFacts', {}).get('name') for n in graph['nodes']]
    assert 'hidden' not in names and 'local' not in names


def test_cpp_multiple_initializers_and_overloads_stay_distinct(tmp_path):
    """Declarator/value pairing and signatures avoid assigning another field's value or merging overloads."""
    graph = capture(tmp_path, '.cpp', 'class C { public: const int A=1, B=2; int read(int x){return A;} int read(double x){return B;} };')
    fields = {n['label']: n for n in graph['nodes'] if n.get('fieldFacts')}
    assert fields['A']['dataFacts']['initializer'] == '1'
    assert fields['B']['dataFacts']['initializer'] == '2'
    assert fields['A']['memberFacts']['constant']
    reads = [n for n in graph['nodes'] if n.get('memberFacts', {}).get('name') == 'read']
    assert len(reads) == 2 and len({n['id'] for n in reads}) == 2


def test_printed_graphify_member_data_survive_long_russian_comment(tmp_path):
    """Actual Graphify sorting/ASCII escaping must not push member/data facts beyond its print cap."""
    import asyncio
    from atlas.mcp_bridge import describe_node
    graph = capture(tmp_path, '.java', 'class C { private static final java.util.Map<String,Integer> CODES=java.util.Map.of("ключ",4); }')
    field = next(n for n in graph['nodes'] if n.get('dataFacts'))
    field['comment'] = 'Выбирает приоритетную бумагу с учётом рынка. ' * 50
    field['commentScope'] = 'containing-type'
    projection = build_mcp_graph(graph)
    attrs = next(n['attributes'] for n in projection['nodes'] if n['id'] == field['id'])
    front = {key: value for key, value in attrs.items() if key.startswith(('atlas.0.', 'atlas.1.', 'atlas.2.'))}
    assert len(json.dumps(front, sort_keys=True)) < 1000
    path = tmp_path / 'mcp-print.json'; path.write_text(json.dumps(projection))
    response = asyncio.run(describe_node(path, field['id']))
    assert 'atlas.0.member' in response['text'] and 'private' in response['text']
    assert 'atlas.1.data' in response['text'] and 'atlas.2.comment' in response['text']
