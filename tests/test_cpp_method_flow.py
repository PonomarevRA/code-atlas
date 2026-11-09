"""C++ adapter acceptance against real host readers; fixtures are parsed, never executed."""
from copy import deepcopy
from pathlib import Path
import sys
import pytest
from atlas.execution_path_source import SourceAnalysis
from atlas.node_description import build_node_description
from atlas.flow_languages.cpp import analyze

FIXTURES = Path(__file__).parent / 'fixtures' / 'flow' / 'cpp'


def direct(code, name='Run', line=1, max_events=160, **extra):
    """Use the adapter contract for selection and budget edge cases."""
    return analyze({'text': code}, {'label': name, 'line': line, **extra}, max_events=max_events)


def through_host(tmp_path, code, node=None, edges=None, targets=None):
    """Exercise safe source reader, SourceAnalysis and production node description."""
    (tmp_path / 'sample.cpp').write_text(code)
    node = node or {'id': 'raw-run', 'label': 'Run', 'file': 'sample.cpp', 'line': 1}
    graph = {'nodes': [node] + (targets or []), 'edges': edges or []}
    saved = deepcopy(graph)
    analysis = SourceAnalysis(tmp_path, 'snapshot', 5, 1_048_576, 160).describe(node)
    result = build_node_description(tmp_path, graph, node['id'], 'snapshot')
    assert graph == saved
    assert (tmp_path / 'sample.cpp').read_text() == code
    assert analysis['methodFlow'] == result['methodFlow']
    assert result['nodeId'] == node['id']
    assert result['sourceByteHash'] and result['sourceHash']
    return result


def flatten(steps):
    """Collect tree rows without implying that alternative arms execute together."""
    result = []
    for step in steps:
        result.append(step)
        for arm in step.get('arms', []):
            result.extend(flatten(arm['steps']))
        for field in ('condition', 'body', 'update'):
            result.extend(flatten(step.get(field, [])))
    return result


def test_host_nested_order_sites_binding_and_source_preserved(tmp_path):
    """Exact nested sites bind only existing raw targets and never mutate source/graph."""
    code = 'int Run(){ return Outer(Inner()); }'
    targets = [{'id': 'raw-inner', 'label': 'Inner', 'file': 'sample.cpp', 'line': 1}]
    edges = [{'source': 'raw-run', 'target': 'raw-inner', 'relation': 'calls', 'line': 1}]
    result = through_host(tmp_path, code, targets=targets, edges=edges)
    assert [e['callee'] for e in result['events']] == ['Inner', 'Outer']
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'call', 'return']
    assert result['events'][0]['targetNodeIds'] == ['raw-inner']
    assert result['events'][1]['targetNodeIds'] == []
    for event, step in zip(result['events'], steps):
        assert step['site'] == {k: event[k] for k in ('line', 'column', 'endLine', 'endColumn')}
    assert result['parameterFacts']['status'] == 'unavailable'
    assert 'C#' not in str(result['attributeFacts'])


def test_fixture_controls_lexical_calls_and_deferred_body(tmp_path):
    """Condition/body/update calls and arm ownership stay distinct through the host."""
    code = (FIXTURES / 'controls.cpp').read_text()
    node = {'id': 'raw-worker-run', 'label': '.Run()', 'file': 'sample.cpp', 'line': 5,
            'callableFacts': {'declaringType': 'Worker', 'parameterList': '(int count)'}}
    result = through_host(tmp_path, code, node=node)
    assert result['sourceStatus'] == 'parsed'
    assert result['documentation']['summary'] == 'Select a result with source-only alternatives.'
    names = [e['callee'] for e in result['events']]
    assert names == ['Prepare', 'Fallback', 'Ready', 'Allowed', 'Inner', 'Accept', 'Reject',
                     'Seed', 'More', 'Skip', 'Work', 'Done', 'Load', 'Use', 'Again', 'Tick',
                     'Once', 'Repeat', 'Code', 'One', 'Other', 'Wrap', 'Finish']
    assert 'Hidden' not in names and 'OtherOverload' not in names
    conditional = {e['callee']: e['conditional'] for e in result['events']}
    assert conditional['Allowed'] and conditional['Accept'] and conditional['Fallback']
    assert not conditional['Ready'] and not conditional['Load']
    steps = result['methodFlow']['steps']
    rows = flatten(steps)
    loops = [s for s in steps if s['kind'] == 'loop']
    assert [s['controlKind'] for s in loops] == ['for', 'foreach', 'while', 'do']
    assert loops[0]['update'][0]['label'] == 'i++'
    assert any(s['kind'] == 'continue' for s in rows)
    switch = next(s for s in steps if s.get('controlKind') == 'switch')
    assert [a['label'] for a in switch['arms']] == ['case 1 / case 2', 'default']
    assert [e['callee'] for e in result['events'] if e['text'] == 'Wrap()'] == ['Wrap']


def test_short_circuit_and_literal_outcomes():
    """Short circuits are not flattened, and value-only arms are not empty skips."""
    result = direct('int Run(){ auto x = Ready() ? 10 : 20; if(A() || B()){ Yes(); } }')
    ternary = result['methodFlow']['steps'][1]
    assert [[s['expression'] for s in a['steps']] for a in ternary['arms']] == [['10'], ['20']]
    logical = next(s for s in result['methodFlow']['steps'] if s.get('controlKind') == '||')
    assert logical['arms'][1]['steps'] == []
    assert logical['arms'][1]['emptyReason'] == 'skip'
    assert [e['conditional'] for e in result['events']] == [False, False, True, True]


def test_identity_overloads_namespaces_same_line_and_stale_range():
    """Name alone cannot choose a same-line overload/type/namespace neighbour."""
    code = 'namespace A {int Run(int x){One();} int Run(double x){Two();}} namespace B {int Run(int x){Three();}}'
    assert direct(code)['sourceStatus'] == 'unresolved'
    chosen = direct(code, name='A::Run', callableFacts={'parameterList': '(double x)'})
    assert [e['callee'] for e in chosen['events']] == ['Two']
    assert direct(code, line=22)['sourceStatus'] == 'unresolved'
    other = direct(code, callableFacts={'declaringType': 'B', 'parameterList': '(int x)'})
    assert [e['callee'] for e in other['events']] == ['Three']
    assert direct('struct A{void Run(){One();}};struct B{void Run(){Two();}};')['sourceStatus'] == 'unresolved'


def test_templates_constructors_and_adjacent_docs():
    """Template declarations keep their own comments and constructors do not expand RAII."""
    code = '// own function\ntemplate<class T> T Run(T x){ return Transform(x); }'
    result = direct(code, line=2)
    assert result['documentation']['summary'] == 'own function'
    assert result['parameters'] == [{'name': 'x', 'type': 'T'}]
    constructor = direct('struct C{C():base(Seed()){Init();}};', name='C')
    assert [e['callee'] for e in constructor['events']] == ['Init']
    assert constructor['methodFlow']['steps'][0]['incomplete']
    assert any('initialization' in s for s in constructor['unresolved'])


@pytest.mark.parametrize('body,missing', [
    ('try { Work(); } catch(...) { Clean(); }', ['Work', 'Clean']),
    ('auto f=[x=Capture()](){Hidden();};', ['Capture', 'Hidden']),
    ('auto n=sizeof(Work());', ['Work']),
    ('switch(Code()){case 1:One();case 2:Two();break;}', ['Code', 'One', 'Two']),
    ('auto p=new T(Make()); delete p;', ['Make']),
])
def test_unsupported_never_becomes_a_false_linear_path(body, missing):
    """Unsupported semantic boundaries remain source rows with concrete reasons."""
    result = direct('void Run(){' + body + ' After();}')
    names = [e['callee'] for e in result['events']]
    assert not set(names).intersection(missing)
    assert names[-1] == 'After'
    assert any(s.get('incomplete') for s in flatten(result['methodFlow']['steps']))


def test_macros_and_preprocessing():
    """Macro arguments and inactive preprocessor branches cannot masquerade as calls."""
    result = direct('#define DROP(x)\nvoid Run(){ DROP(SideEffect()); After(); }', line=2)
    assert [e['callee'] for e in result['events']] == ['After']
    assert any('Macro expansion' in r for r in result['unresolved'])
    result = direct('void Run(){\n#if FLAG\nYes();\n#else\nNo();\n#endif\nAfter();\n}')
    assert [e['callee'] for e in result['events']] == ['After']
    assert any('preproc_' in r for r in result['unresolved'])
    assert direct('#if FLAG\nvoid Run(){Work();}\n#endif', line=2)['sourceStatus'] == 'unresolved'


def test_error_missing_grammar_and_limits(monkeypatch):
    """Parse/AST/step/depth/event failures stay bounded and visibly incomplete."""
    assert direct('void Run(){ Broken(; }')['sourceStatus'] == 'unresolved'
    missing = direct('void Other(){}')
    assert not missing['events'] and missing['methodFlow']['status'] == 'unavailable'
    limited = direct('void Run(){A();B();C();}', max_events=1)
    assert limited['eventsTruncated'] and len(limited['events']) == 1
    assert limited['methodFlow']['truncated']
    large = direct('void Run(){' + 'A();' * 300 + '}')
    assert large['methodFlow']['truncated']
    assert len(flatten(large['methodFlow']['steps'])) <= 180
    assert direct('void Run(){' + 'int a;' * 10000 + '}')['methodFlow']['truncated']
    deep = direct('void Run(){' + 'if(x){' * 40 + 'A();' + '}' * 40 + '}')
    assert deep['methodFlow']['truncated']
    monkeypatch.setitem(sys.modules, 'tree_sitter_cpp', None)
    assert direct('void Run(){}')['sourceStatus'] == 'unavailable'


def test_terminal_jumps_and_no_match():
    """A direct jump has no local fallthrough, and a switch without default may skip."""
    result = direct('int Run(){switch(x){case 1:One();break;} return Done(); Unreachable();}')
    assert [e['callee'] for e in result['events']] == ['One', 'Done']
    switch = result['methodFlow']['steps'][0]
    assert switch['arms'][-1]['emptyReason'] == 'skip'
    assert [s['kind'] for s in result['controlFlow']['terminals']] == ['break', 'return']


def test_real_member_graph_raw_identity_and_class_boundary(tmp_path):
    """Real captured member records reach the adapter without a synthetic ID convention."""
    from atlas.member_graph import enrich_member_graph
    code = 'class C { public: C(){Init();} int Run(int x){return Wrap(x);} };'
    (tmp_path / 'sample.cpp').write_text(code)
    original = {'nodes': [{'id': 'file-raw', 'label': 'sample.cpp', 'file': 'sample.cpp', 'kind': 'file', 'line': 1}], 'edges': []}
    graph = enrich_member_graph(original, tmp_path)
    saved = deepcopy(graph)
    method = next(n for n in graph['nodes'] if (n.get('memberFacts') or {}).get('name') == 'Run')
    result = build_node_description(tmp_path, graph, method['id'], 'snapshot')
    assert result['nodeId'] == method['id']
    assert [e['callee'] for e in result['events']] == ['Wrap']
    assert result['sourceStatus'] == 'parsed'
    owner = next(n for n in graph['nodes'] if n.get('_callable_class'))
    class_result = build_node_description(tmp_path, graph, owner['id'], 'snapshot')
    assert class_result['sourceStatus'] == 'unresolved'
    assert not class_result['events']
    assert graph == saved


def test_cpp_semantic_traps_and_control_metadata():
    """Compile-time syntax, jumps and dropped budget rows never gain false call sites."""
    result = direct('void Run(){static_assert(Check()); typeid(Work()); co_await Async(); After();}')
    assert [e['callee'] for e in result['events']] == ['After']
    loop = direct('void Run(){for(;;){if(Ready())continue; Work();}}')
    arms = {a['id'] for b in loop['controlFlow']['branches'] for a in b['arms']}
    for event in loop['events']:
        assert set(event['branchIds']).issubset(arms)
    assert loop['methodFlow']['steps'][0]['canExit'] is False
    limited = direct('void Run(){' + 'A();' * 178 + 'if(x){B();C();}}')
    sites = [s['site'] for s in flatten(limited['methodFlow']['steps']) if s['kind'] == 'call']
    assert limited['methodFlow']['truncated']
    assert all({k: e[k] for k in ('line', 'column', 'endLine', 'endColumn')} in sites for e in limited['events'])


def test_documentation_owner_and_byte_columns():
    """Class documentation is not inherited; UTF-8 locations select the original call bytes."""
    result = direct('// class docs\nclass C {\n int Run(){const char* s="ёж"; Work();}\n};', line=3)
    assert result['documentation']['summary'] == ''
    event = result['events'][0]
    code_line = ' int Run(){const char* s="ёж"; Work();}'.encode('utf-8')
    assert code_line[event['column'] - 1:event['endColumn'] - 1] == b'Work()'


def test_unsequenced_arguments_not_advertised_as_runtime_order():
    """Nested arguments precede the outer call only in a clearly labelled static presentation."""
    result = direct('void Run(){Outer(Left(), Right());}')
    assert [e['callee'] for e in result['events']] == ['Left', 'Right', 'Outer']
    assert all(e['evaluationOrder'] == 'lexical-not-runtime' for e in result['events'])
    assert any('unspecified' in reason for reason in result['unresolved'])


def test_chained_receivers_repeated_calls_and_destructor():
    """Calls with common starts retain distinct exact ends; destructor bodies parse directly."""
    result = direct('void Run(){Build().Send(); Again(); Again();}')
    assert [e['callee'] for e in result['events']] == ['Build', 'Send', 'Again', 'Again']
    locations = [{k: e[k] for k in ('line', 'column', 'endLine', 'endColumn')} for e in result['events']]
    assert locations[0]['column'] == locations[1]['column']
    assert locations[0]['endColumn'] != locations[1]['endColumn']
    assert len({tuple(s.values()) for s in locations}) == 4
    destructor = direct('struct C{~C(){Clean();}};', name='~C')
    assert [e['callee'] for e in destructor['events']] == ['Clean']
    assert any('RAII' in r for r in destructor['unresolved'])


def test_source_budgets_and_stale_metrics(tmp_path):
    """The shared host owns byte limits and snapshot metric invalidation for C++ too."""
    code = 'void Run(){Work();}'
    (tmp_path / 'sample.cpp').write_text(code)
    node = {'id': 'raw', 'label': 'Run', 'line': 1, 'file': 'sample.cpp',
            'complexity': {'score': 1, 'sourceHash': 'old'}, 'health': {'score': 99, 'sourceHash': 'old'}}
    analysis = SourceAnalysis(tmp_path, 's', 1, 1, 160)
    assert analysis.describe(node)['sourceStatus'] == 'unavailable'
    assert analysis.truncation['bytes']
    result = SourceAnalysis(tmp_path, 's', 1, 1000, 160).describe(node)
    assert result['complexity']['status'] == 'unavailable'
    assert result['health']['status'] == 'unavailable'


def test_adversarial_bounds_include_nested_steps_and_labels():
    """Every visible tree row shares a budget, and huge names/expressions remain bounded."""
    name = 'a' * 1000
    result = direct('void Run(){' + name + '();}')
    event = result['events'][0]
    assert len(event['text']) <= 400 and len(event['sourceExpression']) <= 400
    large = direct('void Run(){' + 'A();' * 177 + 'if(x){B();}else{C();}}')
    assert len(flatten(large['methodFlow']['steps'])) <= 180
    for step in flatten(large['methodFlow']['steps']):
        assert len(step['label']) <= 400


def test_unsupported_abrupt_jumps_do_not_claim_local_fallthrough():
    """Unknown goto destinations and coroutine exits cannot continue as ordinary statements."""
    jump = direct('void Run(){goto target; FalsePath(); target: Work();}')
    assert jump['events'] == []
    assert jump['methodFlow']['steps'][0]['incomplete']
    coroutine = direct('void Run(){co_return Result(); FalsePath();}')
    assert coroutine['events'] == []
    assert coroutine['methodFlow']['steps'][0]['kind'] == 'return'
    assert coroutine['methodFlow']['steps'][0]['incomplete']
