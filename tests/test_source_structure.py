"""Наследование, таблицы и константы сохраняют источники и не исполняют анализируемый код."""
from copy import deepcopy

from atlas.attribute_graph import enrich_attribute_graph
from atlas.execution_path import build_execution_path
from atlas.interface_dispatch import enrich_interface_dispatch
from atlas.node_description import build_node_description
from tests.test_interface_dispatch import fixture


def capture(tmp_path, source):
    """Реальные C# AST и оба production-pass обогащают сырые ID fixture."""
    graph, _ = fixture(tmp_path, source)
    before = deepcopy(graph)
    result = enrich_attribute_graph(enrich_interface_dispatch(graph, tmp_path), tmp_path)
    assert graph == before
    return result


def test_static_literals_tables_factory_and_method_reads(tmp_path):
    graph = capture(tmp_path, '''using System.Collections.Generic;
class C {
 const string Suffix = "TOM", Other = "SMALL";
 static readonly Dictionary<string,int> Priorities = new() { { "a", 1 }, { "a", 2 }, ["b"] = 3 };
 static readonly Dictionary<string,string[]> Codes = CreateCodes();
 Dictionary<string,int> Instance = new() { { "ignored", 9 } };
 static Dictionary<string,string[]> CreateCodes() { return new(); }
 void Run() { var result = Priorities[Suffix]; var next = Codes[Other]; }
 void Shadow() { string Suffix = "local"; var result = Suffix; }
}''')
    by_name = {n['label']: n for n in graph['nodes']}
    assert by_name['Instance']['dataFacts']['kind'] == 'dictionary'
    assert 'staticData' not in by_name['Instance']
    suffix = by_name['Suffix']
    assert suffix['staticData']['initializer'] == '"TOM"'
    assert suffix['fieldFacts']['staticData'] == suffix['staticData']
    table = by_name['Priorities']['staticData']
    assert [(v['key'], v['value']) for v in table['entries']] == [('"a"', '1'), ('"a"', '2'), ('"b"', '3')]
    assert not table['truncated']
    assert by_name['Codes']['staticData']['entries'] == []
    assert by_name['Codes']['staticData']['initializer'] == 'CreateCodes()'
    assert any(e['source'] == by_name['Codes']['id'] and e['target'] == by_name['.CreateCodes()']['id']
               and e['relation'] == 'initializes_from' for e in graph['edges'])
    reads = {(e['source'], e['target']) for e in graph['edges'] if e['relation'] == 'reads'}
    assert (by_name['.Run()']['id'], suffix['id']) in reads
    assert (by_name['.Shadow()']['id'], suffix['id']) not in reads
    path = build_execution_path(tmp_path, graph, by_name['.Run()']['id'], 'captured', max_depth=5)
    fields = [step for step in path['steps'] if step['kind'] == 'field']
    assert len(fields) == 4 and all(step['sourceKind'] == 'snapshot-captured-source' for step in fields)
    assert any(step['label'] == '.CreateCodes()' for step in path['steps'])
    (tmp_path / 'Types.cs').write_text('class C { const string Suffix = "changed"; }')
    described = build_node_description(tmp_path, graph, suffix['id'], 'captured')
    assert described['staticData']['initializer'] == '"TOM"'
    assert described['sourceKind'] == 'snapshot-captured-source', 'captured field value must not pretend to be a live-source fact'


def test_inheritance_generic_parent_imports_and_original_ids(tmp_path):
    graph = capture(tmp_path, '''using A;
namespace A { class Parent<T> { void BaseMethod() {} } }
namespace B { class Parent<T> {} class Local : Parent<External> {} }
namespace C { class Child : Parent<External> { void Own() {} } }
''')
    parents = [n for n in graph['nodes'] if n['label'] == 'Parent']
    by_name = {n['label']: n for n in graph['nodes']}
    inheritance = [e for e in graph['edges'] if e['relation'] == 'inherits']
    assert {(e['source'], e['target']) for e in inheritance} == {
        (by_name['Local']['id'], parents[1]['id']), (by_name['Child']['id'], parents[0]['id'])}
    assert all(e['evidence']['declaredType'] == 'Parent<External>' for e in inheritance)
    path = build_execution_path(tmp_path, graph, by_name['Child']['id'], 's')
    assert path['rootKind'] == 'class'
    assert any(e['relation'] == 'inherits' for e in path['edges'])
    assert any(step['label'] == '.BaseMethod()' for step in path['steps'])


def test_qualified_static_read_and_ambiguous_parent_are_not_guessed(tmp_path):
    graph = capture(tmp_path, '''using A; using B;
namespace A { class Parent {} class Values { public const string Key = "value"; } }
namespace B { class Parent {} }
namespace C { class Child : Parent { string Read() => A.Values.Key; } }
''')
    assert not any(e['relation'] == 'inherits' for e in graph['edges'])
    key = next(n for n in graph['nodes'] if n['label'] == 'Key')
    reader = next(n for n in graph['nodes'] if n['label'] == '.Read()')
    assert any(e['source'] == reader['id'] and e['target'] == key['id'] and e['relation'] == 'reads' for e in graph['edges'])


def test_capture_limits_and_repeated_enrichment_preserve_ids(tmp_path, monkeypatch):
    import atlas.static_data as data
    monkeypatch.setattr(data, 'MAX_ENTRIES', 2)
    graph = capture(tmp_path, '''class C {
 const string Name = nameof(C);
 static Dictionary<string,int> D = new() { { "a", 1 }, { "b", 2 }, { "c", 3 } };
}''')
    table = next(n for n in graph['nodes'] if n['label'] == 'D')['staticData']
    assert len(table['entries']) == 2 and table['truncated']
    assert next(n for n in graph['nodes'] if n['label'] == 'Name')['staticData']['initializer'] == 'nameof(C)'
    repeated = enrich_interface_dispatch(graph, tmp_path)
    assert [n['id'] for n in repeated['nodes']] == [n['id'] for n in graph['nodes']]
    assert repeated['edges'] == graph['edges']


def test_factory_argument_and_selector_are_not_published_as_final_pairs(tmp_path):
    graph = capture(tmp_path, '''class C {
 static Dictionary<string,int> D = Change(new Dictionary<string,int> { { "a", 1 } });
 static FrozenDictionary<string,int> F = new Dictionary<string,int> { { "a", 1 } }.ToFrozenDictionary(x => x.Key, x => x.Value + 1);
}''')
    fields = [n for n in graph['nodes'] if n.get('staticData')]
    assert len(fields) == 2 and all(n['staticData']['entries'] == [] for n in fields)


def test_total_data_budget_marks_incomplete_path(tmp_path, monkeypatch):
    import atlas.source_structure as structure
    monkeypatch.setattr(structure, 'MAX_DATA_CHARS', 7)
    graph = capture(tmp_path, '''class C {
 const string A = "first";
 const string B = "second";
 void Run() { var result = A + B; }
}''')
    assert graph['metadata']['sourceStructure']['truncated']
    assert len([n for n in graph['nodes'] if n.get('staticData')]) == 1
    root = next(n for n in graph['nodes'] if n['label'] == '.Run()')
    assert build_execution_path(tmp_path, graph, root['id'], 's')['truncation']['staticData']


def test_dictionary_factory_writes_stay_inside_the_loop(tmp_path):
    graph = capture(tmp_path, '''class C {
 static Dictionary<string,string[]> Create() {
   var codes = new Dictionary<string,string[]>();
   var other = new List<string>();
   foreach(var currency in Input) { codes[currency] = [currency + "TOM", currency]; }
   other[0] = "ignore";
   return codes;
 }
}''')
    method = next(n for n in graph['nodes'] if n['label'] == '.Create()')
    step = build_execution_path(tmp_path, graph, method['id'], 's')['steps'][0]
    writes = [event for event in step['events'] if event['kind'] == 'data-write']
    assert len(writes) == 1
    assert writes[0]['dataKey'] == 'codes[currency]'
    assert writes[0]['dataValue'] == '[currency + "TOM", currency]'
    assert writes[0]['branchIds'], 'dictionary construction belongs to each loop iteration, not an unconditional step'


def test_constructor_is_not_confused_with_the_containing_class(tmp_path):
    from atlas.execution_path_source import SourceAnalysis
    (tmp_path / 'Ctor.cs').write_text('class Sorter { Sorter(int x) { if(x > 0) Build(); } void Build() {} }')
    analysis = SourceAnalysis(tmp_path, 's', 5, 1024 * 1024, 160)
    constructor = analysis.describe({'id':'ctor','label':'.Sorter()','kind':'symbol','file':'Ctor.cs','line':1})
    owner = analysis.describe({'id':'owner','label':'Sorter','_callable_class':True,'file':'Ctor.cs','line':1})
    assert constructor['kind'] == 'method' and constructor['sourceStatus'] == 'parsed'
    assert len(constructor['controlFlow']['branches']) == 1
    assert owner['kind'] == 'class' and owner['controlFlow']['branches'] == []


def test_static_dictionary_used_as_receiver_is_a_read(tmp_path):
    graph = capture(tmp_path, '''using System.Collections.Generic;
class C {
 static readonly Dictionary<string,int> Codes = new() { { "TQBR", 2 } };
 int Read(string code) { return Codes.GetValueOrDefault(code); }
 bool Check(string code) { return Codes.TryGetValue(code, out var value); }
 int Shadow(string code) { var Codes = new Dictionary<string,int>(); return Codes.GetValueOrDefault(code); }
}''')
    nodes = {n['label']: n for n in graph['nodes']}
    readers = {e['source'] for e in graph['edges'] if e['relation'] == 'reads' and e['target'] == nodes['Codes']['id']}
    assert nodes['.Read()']['id'] in readers
    assert nodes['.Check()']['id'] in readers
    assert nodes['.Shadow()']['id'] not in readers
