"""Контракт JS/JSX: настоящий host/description, точные пути и отсутствие ложных вызовов."""
from copy import deepcopy
from pathlib import Path

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.javascript import analyze
from atlas.member_graph import enrich_member_graph
from atlas.node_description import build_node_description

FIXTURES = Path(__file__).parent / 'fixtures' / 'flow' / 'javascript'


def describe(tmp_path, body=None, *, source=None, label='run', line=1, suffix='.js', facts=None):
    """Проверяет production API, сохранность source/graph и согласованность событий с деревом."""
    source = source if source is not None else 'async function run(){' + body + '}'
    filename = 'Code' + suffix
    path = tmp_path / filename
    path.write_text(source, encoding='utf-8')
    selected = {'id': 'raw:run', 'label': label, 'line': line, 'file': filename, 'kind': 'function', **(facts or {})}
    graph = {'nodes': [selected], 'edges': [], 'metadata': {'snapshotId': 's'}}
    before = deepcopy(graph)
    analysis = SourceAnalysis(tmp_path, 's', 5, 1024 * 1024, 160)
    direct = analysis.describe(selected)
    result = build_node_description(tmp_path, graph, selected['id'], 's')
    assert graph == before and path.read_text(encoding='utf-8') == source
    assert result['nodeId'] == selected['id']
    assert result['methodFlow'] == direct['methodFlow']
    assert result['sourceHash'] == direct['sourceHash']
    assert result['sourceByteHash'] and result['sourceReadAt']
    if result['methodFlow']['status'] == 'parsed' and not result['methodFlow']['truncated']:
        sites = [step['site'] for step in all_steps(result['methodFlow']['steps']) if step['kind'] == 'call']
        event_sites = [{key: event[key] for key in ('line', 'column', 'endLine', 'endColumn')}
                       for event in result['events']]
        assert sites == event_sites
    return result


def all_steps(steps):
    """Обходит альтернативы отдельно; порядок do-condition следует после тела."""
    result = []
    for step in steps:
        result.append(step)
        for arm in step.get('arms', []):
            result.extend(all_steps(arm['steps']))
        fields = ('body', 'condition', 'update') if step.get('controlKind') == 'do' else ('condition', 'body', 'update')
        for field in fields:
            result.extend(all_steps(step.get(field, [])))
    return result


def event_names(result):
    """Возвращает лексические имена в порядке вычисления, не сортируя по offset."""
    return [event['callee'] for event in result['events']]


def test_nested_arguments_receivers_and_direct_await(tmp_path):
    result = describe(tmp_path, 'Before(); Before(); return Wrap(await Receiver().Work(Arg(), Other(Inner())));')
    assert event_names(result) == ['Before', 'Before', 'Receiver', 'Arg', 'Inner', 'Other', 'Work', 'Wrap']
    assert [event['awaited'] for event in result['events']] == [False] * 6 + [True, False]
    assert all(not event['parallel'] for event in result['events'])
    steps = result['methodFlow']['steps']
    assert [step['kind'] for step in steps] == ['call'] * 7 + ['await', 'call', 'return']
    assert steps[0]['site'] != steps[1]['site']
    assert result['parameterFacts']['status'] == result['attributeFacts']['status'] == 'unavailable'
    assert 'C#' not in result['parameterFacts']['warning']


def test_real_fixture_defaults_branches_loops_deferred_and_terminal(tmp_path):
    result = describe(tmp_path, source=(FIXTURES / 'reader.js').read_text(), line=2)
    assert result['sourceStatus'] == 'parsed'
    assert result['documentation']['summary'] == 'Read one input; callbacks remain deferred.'
    assert result['parameters'] == [{'name': 'input', 'type': 'unknown'}]
    assert event_names(result) == ['Seed', 'Before', 'Check', 'Yes', 'No', 'Init', 'CheckLoop',
                                  'Skip', 'Work', 'Done', 'Update', 'Load', 'Use', 'Arg', 'Finish', 'Wrap']
    events = {event['callee']: event for event in result['events']}
    assert events['Seed']['conditional'] and events['Seed']['branchIds']
    assert not events['Check']['conditional'] and not events['Load']['conditional']
    assert all(events[name]['conditional'] for name in ['Yes', 'No', 'CheckLoop', 'Work', 'Use'])
    loops = [step for step in result['methodFlow']['steps'] if step['kind'] == 'loop']
    assert [step['label'] for step in loops[0]['update']] == ['i++', 'Update()']
    assert loops[1]['controlKind'] == 'foreach'
    assert result['methodFlow']['steps'][-1]['kind'] == 'return'
    assert len(result['methodFlow']['unsupported']) == 1
    assert 'class' in result['methodFlow']['unsupported'][0]


def test_short_circuit_and_conditional_are_alternatives(tmp_path):
    result = describe(tmp_path, 'const a=Left() && Right(); const b=Other() || Fallback(); const c=Read() ?? 10; return Test() ? Yes() : No();')
    assert event_names(result) == ['Left', 'Right', 'Other', 'Fallback', 'Read', 'Test', 'Yes', 'No']
    assert [event['conditional'] for event in result['events']] == [False, True, False, True, False, False, True, True]
    branches = [step for step in result['methodFlow']['steps'] if step['kind'] == 'branch']
    assert [step['controlKind'] for step in branches] == ['&&', '||', '??', 'conditional']
    assert all(branch['arms'][1]['emptyReason'] == 'skip' for branch in branches[:3])
    assert branches[2]['arms'][0]['steps'][0]['expression'] == '10'
    assert [[step['label'] for step in arm['steps']] for arm in branches[-1]['arms']] == [['Yes()'], ['No()']]


def test_optional_chain_suffix_and_parenthesized_boundary(tmp_path):
    result = describe(tmp_path, 'Receiver()?.send(Arg()).next(More()); (receiver?.send)(Always()); obj?.[Index()]?.(Param());')
    assert event_names(result) == ['Receiver', 'Arg', 'send', 'More', 'next', 'Always', '', 'Index', 'Param', '']
    assert [event['conditional'] for event in result['events']] == [False, True, True, True, True, False, False, True, True, True]
    assert result['events'][1]['branchIds'] == result['events'][4]['branchIds']
    assert len(result['events'][-1]['branchIds']) == 2
    optional = result['methodFlow']['steps'][1]
    assert [step['label'] for step in optional['arms'][0]['steps']] == ['Arg()', 'Receiver()?.send()', 'More()', 'Receiver()?.send(Arg()).next()']
    assert optional['arms'][1]['emptyReason'] == 'skip'


def test_arrow_and_function_expression_selection(tmp_path):
    source = 'const run = async (input) => Wrap(await Work(input));'
    result = describe(tmp_path, source=source)
    assert result['sourceStatus'] == 'parsed'
    assert event_names(result) == ['Work', 'Wrap']
    assert result['methodFlow']['steps'][-1]['implicit']
    assert result['parameters'] == [{'name': 'input', 'type': 'unknown'}]
    result = describe(tmp_path, source='const run = function named(a){ return Work(a); };')
    assert result['sourceStatus'] == 'parsed' and event_names(result) == ['Work']


def test_try_finally_and_generator_are_opaque(tmp_path):
    result = describe(tmp_path, 'Before(); try {return Work();} catch(e){Fail(e);} finally {Clean();} After();')
    assert event_names(result) == ['Before', 'After']
    step = result['methodFlow']['steps'][1]
    assert step['incomplete'] and 'finally' in step['unsupported']
    result = describe(tmp_path, source='function* run(){Before();yield Work();After();}')
    assert not result['events']
    assert result['methodFlow']['steps'][0]['incomplete']
    assert 'Generator' in result['methodFlow']['unsupported'][0]


def test_immediate_and_deferred_callbacks_never_inline_the_body(tmp_path):
    result = describe(tmp_path, 'Schedule(()=>Deferred()); (function(){ Hidden(); })(); const o={method(){HiddenMethod();}}; new C(Arg());')
    assert event_names(result) == ['Schedule', '', 'Arg', 'C']
    assert all('Hidden' not in event['text'] and 'Deferred' not in event['callee'] for event in result['events'])


def test_loops_do_continue_and_break_are_not_flattened(tmp_path):
    result = describe(tmp_path, 'while(Check()){Work(); continue; Hidden();} do{Run();}while(More()); for(Init();Test();Update()){break;Hidden();} After();')
    assert event_names(result) == ['Check', 'Work', 'Run', 'More', 'Init', 'Test', 'After']
    steps = result['methodFlow']['steps']
    assert steps[0]['body'][-1]['kind'] == 'continue'
    assert steps[1]['controlKind'] == 'do'
    assert steps[3]['body'][-1]['kind'] == 'break'
    assert all(step['kind'] != 'call' for step in steps[0]['body'][2:])


def test_if_both_arms_terminal_and_throw_arguments(tmp_path):
    result = describe(tmp_path, 'if(Check()){return Yes();}else{throw new Error(Arg());} Hidden();')
    assert event_names(result) == ['Check', 'Yes', 'Arg', 'Error']
    assert result['methodFlow']['steps'][-1]['kind'] == 'branch'
    assert [terminal['kind'] for terminal in result['controlFlow']['terminals']] == ['return', 'throw']


def test_duplicate_names_exact_line_owner_and_no_neighbor_fallback(tmp_path):
    source = 'class A {\n run(){One();}\n}\nclass B {\n run(){Two();}\n}'
    result = describe(tmp_path, source=source, line=5, facts={'callableFacts': {'declaringType': 'B'}})
    assert event_names(result) == ['Two']
    result = describe(tmp_path, source=source, line=2, facts={'callableFacts': {'declaringType': 'B'}})
    assert result['sourceStatus'] == 'unresolved' and not result['events']
    result = analyze({'text': source}, {'label': 'run'}, max_events=10)
    assert result['sourceStatus'] == 'unresolved'
    result = describe(tmp_path, source=source, line=4)
    assert result['sourceStatus'] == 'unresolved'


def test_same_line_and_nested_declarations_remain_ambiguous(tmp_path):
    source = 'function outer(){ function run(){Inner();} } function run(){Outer();}'
    result = describe(tmp_path, source=source)
    assert result['sourceStatus'] == 'unresolved'
    column = source.index('function run(){Outer') + 1
    result = describe(tmp_path, source=source, facts={'column': column})
    assert event_names(result) == ['Outer']


@pytest.mark.parametrize('suffix', ['.js', '.mjs', '.cjs', '.jsx'])
def test_all_extensions_route_through_host(tmp_path, suffix):
    result = describe(tmp_path, 'Work();', suffix=suffix)
    assert result['analyzerVersion'].startswith('javascript-')
    assert result['sourceStatus'] == 'parsed' and event_names(result) == ['Work']


def test_jsx_expressions_are_eager_without_inventing_component_calls(tmp_path):
    result = describe(tmp_path, source=(FIXTURES / 'choices.jsx').read_text(), suffix='.jsx', label='render', line=4)
    assert event_names(result) == ['Before', 'Read', 'Content']
    assert not result['methodFlow']['unsupported']
    assert result['methodFlow']['steps'][-2]['label'].startswith('JSX:')
    assert result['methodFlow']['steps'][-1]['kind'] == 'return'


@pytest.mark.parametrize('body,reason', [
    ('switch(Code()){case Match():One();case 2:Two();break;default:Other();}', 'switch'),
    ('const {x=Hidden()}=Read();', 'Destructuring'),
    ('target ||= Hidden();', 'Logical assignment'),
    ('outer: while(Check()){break outer;}', 'labeled'),
    ('for await(const x of Load()){Work();}', 'for await'),
    ('tag`value ${Hidden()}`;', 'Tagged template'),
])
def test_unsupported_constructs_do_not_claim_inner_calls(tmp_path, body, reason):
    result = describe(tmp_path, body)
    assert not result['events']
    assert any(reason in item for item in result['methodFlow']['unsupported'])
    assert result['methodFlow']['steps'][0]['incomplete']


def test_parse_error_missing_name_and_missing_grammar(tmp_path, monkeypatch):
    result = describe(tmp_path, source='function run(){ Call( ; }')
    assert result['sourceStatus'] == 'unresolved' and not result['events']
    assert 'parse error' in result['unresolved'][0]
    result = describe(tmp_path, source='function other(){Call();}')
    assert result['sourceStatus'] == 'unresolved'
    import sys
    monkeypatch.setitem(sys.modules, 'tree_sitter_javascript', None)
    result = analyze({'text': 'function run(){}'}, {'label': 'run'}, max_events=5)
    assert result['sourceStatus'] == 'unavailable'


def test_step_event_ast_and_depth_budgets(tmp_path):
    result = describe(tmp_path, 'Call();' * 220)
    assert result['methodFlow']['truncated'] and len(result['methodFlow']['steps']) == 180
    result = analyze({'text': 'function run(){A(); B(); C();}'}, {'label': 'run', 'line': 1}, max_events=1)
    assert len(result['events']) == 1 and result['eventsTruncated']
    assert len(result['methodFlow']['steps']) == 3
    result = analyze({'text': 'function run(){' + 'A();' * 5000 + '}'}, {'label': 'run'}, max_events=10)
    assert result['sourceStatus'] == 'unavailable' and result['methodFlow']['truncated']
    result = describe(tmp_path, 'return ' + 'A(' * 35 + '0' + ')' * 35 + ';')
    assert result['methodFlow']['truncated']
    assert any('глубина' in item for item in result['methodFlow']['unsupported'])
    assert not result['events']
    assert all(step['kind'] != 'return' for step in result['methodFlow']['steps'])


def test_unicode_byte_sites_match_source(tmp_path):
    result = describe(tmp_path, 'const привет="мир"; Work(Арг());')
    content = (tmp_path / 'Code.js').read_bytes()
    for event in result['events']:
        raw = content.splitlines()[event['line'] - 1][event['column'] - 1:event['endColumn'] - 1]
        assert raw.decode('utf-8') == event['sourceExpression']


def test_real_member_map_and_existing_raw_id_binding(tmp_path):
    source = 'class Worker {\n run(){Work(Arg());}\n Work(value){}\n}'
    (tmp_path / 'Code.js').write_text(source)
    raw = {'nodes': [{'id': 'file', 'label': 'Code.js', 'file': 'Code.js', 'kind': 'file', 'line': 1},
                     {'id': 'upstream:run', 'label': 'run', 'file': 'Code.js', 'kind': 'function', 'line': 2},
                     {'id': 'upstream:work', 'label': 'Work', 'file': 'Code.js', 'kind': 'function', 'line': 3}],
           'edges': [{'source': 'upstream:run', 'target': 'upstream:work', 'relation': 'calls', 'source_line': 2}]}
    graph = enrich_member_graph(raw, tmp_path)
    before = deepcopy(graph)
    result = build_node_description(tmp_path, graph, 'upstream:run', 's')
    assert event_names(result) == ['Arg', 'Work']
    assert result['events'][-1]['targetNodeIds'] == ['upstream:work']
    assert graph == before and (tmp_path / 'Code.js').read_text() == source


def test_export_own_documentation_and_signature(tmp_path):
    source = '/** Class documentation */\nclass C {\n /** Own method. */\n run(value){Work();}\n}\n/** Own arrow. */\nexport const arrow = (value) => Read(value);'
    result = describe(tmp_path, source=source, line=4)
    assert result['documentation']['summary'] == 'Own method.'
    result = describe(tmp_path, source=source, line=7, label='arrow')
    assert result['documentation']['summary'] == 'Own arrow.'
    assert result['signature'] == '(value) =>'


def test_optional_property_value_and_nested_expression_condition(tmp_path):
    result = describe(tmp_path, 'if(Left() && Right()){Work();} return Read()?.field;')
    steps = result['methodFlow']['steps']
    assert steps[2]['conditionResult'] == 'reuse'
    assert steps[2]['conditionExpression'] == '(Left() && Right())'
    value = steps[-2]['arms'][0]['steps'][0]
    assert value['kind'] == 'value' and value['reuseResult']
    assert steps[-2]['arms'][1]['emptyReason'] == 'skip'
    assert event_names(result) == ['Left', 'Right', 'Work', 'Read']


def test_iteration_exit_suppresses_checks_updates_and_continue_retains_update(tmp_path):
    result = describe(tmp_path, 'do { break; } while(Hidden()); for(;;){continue;} for(;;){break;} for(;Check();Update()){continue;}')
    steps = result['methodFlow']['steps']
    assert event_names(result) == ['Check', 'Update']
    assert steps[0]['condition'] == []
    # canExit controls the UI test-false port; explicit breaks have their own join edge.
    assert not steps[1]['canExit'] and not steps[2]['canExit']
    assert steps[-1]['update'][0]['label'] == 'Update()'
    result = describe(tmp_path, 'for(;Check();Hidden()){if(Test()){return One();}else{throw Error();}}')
    assert event_names(result) == ['Check', 'Test', 'One', 'Error']
    assert result['methodFlow']['steps'][0]['update'] == []


def test_computed_method_keys_are_immediate_but_defaults_and_bodies_deferred(tmp_path):
    result = describe(tmp_path, 'const obj={ [Key()](a=Default()){Hidden();}, [Other()]:Value() };')
    assert event_names(result) == ['Key', 'Other', 'Value']
    assert not result['methodFlow']['unsupported']
    result = describe(tmp_path, 'class C extends Base(){ static x=Init(); [Computed()](){Hidden();} }')
    assert not result['events'] and 'class' in result['methodFlow']['unsupported'][0]
    assert result['methodFlow']['steps'][0]['label'].startswith('Не извлечено:')


def test_parenthesized_await_and_delete_preserve_semantics(tmp_path):
    result = describe(tmp_path, 'await /* outer comment */ ( /* comment */ Work(Arg())); delete obj[Key()];')
    assert event_names(result) == ['Arg', 'Work', 'Key']
    assert [event['awaited'] for event in result['events']] == [False, True, False]
    assert result['methodFlow']['steps'][-1]['label'] == 'delete obj[Key()];'


def test_parameter_destructuring_is_visible_and_defaults_remain_conditional(tmp_path):
    result = describe(tmp_path, source='const run=({a=Hidden()}=Seed())=>Work();')
    assert event_names(result) == ['Seed', 'Work']
    assert result['events'][0]['conditional']
    assert any('Destructuring parameter' in reason for reason in result['methodFlow']['unsupported'])


def test_iteration_binding_evaluation_follows_iterable_once(tmp_path):
    result = describe(tmp_path, 'for(obj[Key()] of Load()){Work();}')
    assert event_names(result) == ['Load', 'Key', 'Work']
    assert [event['conditional'] for event in result['events']] == [False, True, True]
    result = describe(tmp_path, 'for(const {x=Hidden()} of Load()){Work();}')
    assert event_names(result) == ['Load', 'Work']
    assert any('Destructuring iteration' in reason for reason in result['methodFlow']['unsupported'])


def test_constructor_and_async_generator_declaration_boundaries(tmp_path):
    result = describe(tmp_path, source='class C {constructor(){Work();}}', label='constructor')
    assert result['kind'] == 'constructor' and event_names(result) == ['Work']
    result = describe(tmp_path, source='class C { async *run(){Before();yield Work();} }')
    assert not result['events'] and result['methodFlow']['status'] == 'declaration-only'


def test_long_text_and_documentation_limits_are_explicit(tmp_path):
    source = '/** ' + 'a' * 9000 + ' */\nfunction run(){Work("' + 'b' * 450 + '");}'
    result = describe(tmp_path, source=source, line=2)
    assert result['documentation']['truncated']
    assert len(result['documentation']['comments'][0]['text']) <= 8000
    assert any('400' in reason for reason in result['methodFlow']['unsupported'])
    for step in all_steps(result['methodFlow']['steps']):
        assert len(step['label']) <= 400


def test_jsx_file_arrow_remains_an_analyzable_callable(tmp_path):
    result = describe(tmp_path, source=(FIXTURES / 'choices.jsx').read_text(), suffix='.jsx', label='choose')
    assert event_names(result) == ['Arg', 'send', 'More', 'next', 'Fallback']
    assert all(event['conditional'] for event in result['events'])
    assert result['methodFlow']['steps'][-1]['implicitReturn']


def test_await_constructor_and_argument_comments(tmp_path):
    result = describe(tmp_path, 'await new Promise(/* a */ Arg(), /* b */ Other());')
    assert event_names(result) == ['Arg', 'Other', 'Promise']
    assert [event['awaited'] for event in result['events']] == [False, False, True]
    assert [argument['index'] for argument in result['events'][-1]['arguments']] == [0, 1]


def test_nested_jsx_attribute_spread_conditions_and_deferred_handlers(tmp_path):
    source = 'function run(){const Widget=()=>Hidden(); return <Panel a={Attr()} {...Props()} flag><Inner b={Child()} />{ok && Yes()}<Widget onClick={()=>Deferred()}/></Panel>;}'
    result = describe(tmp_path, source=source, suffix='.jsx')
    assert event_names(result) == ['Attr', 'Props', 'Child', 'Yes']
    assert [event['conditional'] for event in result['events']] == [False, False, False, True]
    assert not result['methodFlow']['unsupported']
