"""Реальные C# AST отделяют создание объекта от отдельных вызовов методов."""
from copy import deepcopy

from atlas.execution_path import build_execution_path
from atlas.interface_dispatch import enrich_interface_dispatch
from tests.test_interface_dispatch import fixture


def test_missing_generic_constructor_base_and_body_calls(tmp_path):
    source = '''using System.Collections.Generic;
class Base<T> {
 public Base(IEnumerable<T> items, int mode = 0) {
  foreach(var item in items) { if (mode > 0) Add(item); }
  var lazy = new System.Lazy<int>(() => Later());
 }
 void Add(T item) {} int Later() => 1;
 public int Find() => 1;
}
class Child : Base<int> {
 public Child(IEnumerable<int> items) : base(items) {}
}'''
    graph, _ = fixture(tmp_path, source)
    before = deepcopy(graph)
    result = enrich_interface_dispatch(graph, tmp_path)
    assert graph == before
    constructors = [n for n in result['nodes'] if n.get('kind') == 'constructor']
    assert len(constructors) == 2
    child = next(n for n in constructors if n['label'].startswith('Child('))
    parent = next(n for n in constructors if n['label'].startswith('Base('))
    assert child['id'] != next(n['id'] for n in graph['nodes'] if n['label'] == 'Child')
    calls = [e for e in result['edges'] if e['relation'] == 'calls']
    assert any(e['source'] == child['id'] and e['target'] == parent['id'] and e['evidence']['callKind'] == 'constructor-initializer' for e in calls)
    add = next(n['id'] for n in graph['nodes'] if n['label'] == '.Add()')
    later = next(n['id'] for n in graph['nodes'] if n['label'] == '.Later()')
    assert any(e['source'] == parent['id'] and e['target'] == add for e in calls)
    assert not any(e['source'] == parent['id'] and e['target'] == later for e in calls), 'deferred lambda is not a constructor-body call'
    path = build_execution_path(tmp_path, result, child['id'], 's')
    event = next(e for e in path['steps'][0]['events'] if e.get('callKind') == 'constructor-initializer')
    assert event['targetNodeIds'] == [parent['id']]
    assert next(s for s in path['steps'] if s['nodeId'] == parent['id'])['controlFlow']['branches']
    assert next(e for s in path['steps'] if s['nodeId'] == parent['id'] for e in s['events'] if e.get('callee') == 'Later')['deferred']
    repeat = enrich_interface_dispatch(result, tmp_path)
    assert {n['id'] for n in repeat['nodes']} == {n['id'] for n in result['nodes']}
    assert len(repeat['edges']) == len(result['edges'])


def test_constructor_overloads_are_not_guessed_and_this_is_separate(tmp_path):
    graph, _ = fixture(tmp_path, '''class Parent {
 public Parent(int v) {} public Parent(string v) {}
}
class Child : Parent { public Child(int v) : base(v) {} }
class Chain { public Chain() : this(1) {} public Chain(int v) {} }''')
    result = enrich_interface_dispatch(graph, tmp_path)
    child = next(n for n in result['nodes'] if n.get('kind') == 'constructor' and n['label'].startswith('Child'))
    assert not any(e['source'] == child['id'] and e['relation'] == 'calls' for e in result['edges'])
    assert len([n for n in result['nodes'] if n.get('kind') == 'constructor' and n['label'].startswith('Chain')]) == 2


def test_static_instance_same_line_and_ambiguous_helper(tmp_path):
    graph, _ = fixture(tmp_path, 'class C { static C() { Init(); } public C() { Run("x"); } void Run(object v) {} void Run(string v, int x = 0) {} static void Init() {} }')
    result = enrich_interface_dispatch(graph, tmp_path)
    constructors = [n for n in result['nodes'] if n.get('kind') == 'constructor']
    assert len(constructors) == 2 and len({n['id'] for n in constructors}) == 2
    for constructor in constructors:
        path = build_execution_path(tmp_path, result, constructor['id'], 's')
        assert path['steps'][0]['sourceStatus'] == 'parsed'
    instance = next(n for n in constructors if 'static' not in n['callableFacts']['modifiers'])
    assert not any(e['source'] == instance['id'] and e['relation'] == 'calls' for e in result['edges']), 'optional overloads require binding, not arity guessing'
