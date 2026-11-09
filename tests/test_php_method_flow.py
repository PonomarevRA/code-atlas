"""PHP AST fixtures exercise production descriptions; fixture code is never executed."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.php import analyze
from atlas.node_description import build_node_description
from tests.test_member_graph import capture

FIXTURE = Path(__file__).parent / 'fixtures/flow/php/reader.php'


def direct(body, *, max_events=160, label='run', line=1):
    """Parse an inert function, with exact source identity and no PHP interpreter."""
    source = {'text': '<?php function run(){' + body + '}', 'file': 'Code.php'}
    return analyze(source, {'id': 'raw-run', 'label': label, 'line': line}, max_events=max_events)


def flatten(steps):
    """Traverse each alternative without treating alternatives as a runtime trace."""
    result = []
    for step in steps:
        result.append(step)
        for arm in step.get('arms', []):
            result.extend(flatten(arm['steps']))
        for key in ('condition', 'body', 'update'):
            result.extend(flatten(step.get(key, [])))
    return result


def names(result):
    """Read actual event order rather than sorting call start coordinates."""
    return [event['callee'] for event in result['events']]


def test_production_source_analysis_and_description_preserve_raw_graph(tmp_path):
    """Real member graph IDs bind only graph-backed calls; graph and source remain intact."""
    source = FIXTURE.read_text()
    graph = capture(tmp_path, '.php', source)
    run = next(node for node in graph['nodes'] if (node.get('memberFacts') or {}).get('name') == 'run')
    wrap = next(node for node in graph['nodes'] if (node.get('memberFacts') or {}).get('name') == 'Wrap')
    graph['edges'].append({'source': run['id'], 'target': wrap['id'], 'relation': 'calls', 'line': 28})
    before = deepcopy(graph)
    analysis = SourceAnalysis(tmp_path, 'php-snapshot', 5, 1024 * 1024, 160)
    local = analysis.describe(run)
    result = build_node_description(tmp_path, graph, run['id'], 'php-snapshot')
    assert local['sourceStatus'] == result['sourceStatus'] == 'parsed'
    assert result['nodeId'] == run['id'] and result['snapshotId'] == 'php-snapshot'
    assert result['sourceHash'] == sha256(source.encode()).hexdigest()
    assert result['sourceByteHash'] and result['sourceReadAt']
    assert result['signature'] == 'public function run(?Service $service, int $count = 2): ?string'
    assert result['parameters'] == [{'name': '$service', 'type': '?Service'}, {'name': '$count', 'type': 'int'}]
    assert result['returnType'] == '?string'
    assert result['documentation']['summary'] == 'Возвращает выбранное значение.'
    assert result['typeContext']['documentation']['summary'] == 'Контекст Reader.'
    assert result['comment'] == 'Возвращает выбранное значение.'
    assert result['parameterFacts']['status'] == result['attributeFacts']['status'] == 'unavailable'
    assert names(local) == ['Before', 'Check', 'Yes', 'Other', 'No', 'Last', 'First', 'Fallback',
                            'Argument', 'load', 'Init', 'Test', 'Skip', 'Work', 'Done', 'Update',
                            'Load', 'UseItem', 'Inner', 'Wrap']
    event = next(event for event in result['events'] if event['callee'] == 'Wrap')
    assert event['targetNodeIds'] == [wrap['id']]
    assert event['confidence'] == 'graph' and event['line'] == 28
    assert all(set(event['targetNodeIds']) <= {node['id'] for node in graph['nodes']} for event in result['events'])
    tree_calls = [step for step in flatten(result['methodFlow']['steps']) if step['kind'] == 'call']
    sites = {tuple(event[key] for key in ('line', 'column', 'endLine', 'endColumn')) for event in result['events']}
    assert len(tree_calls) == len(sites)
    assert all(tuple(step['site'].values()) in sites for step in tree_calls)
    arm_ids = {arm['id'] for branch in result['controlFlow']['branches'] for arm in branch['arms']}
    assert all(set(event['branchIds']) <= arm_ids for event in result['events'])
    assert graph == before and (tmp_path / 'Code.php').read_text() == source


def test_nested_arguments_receiver_and_repeated_calls():
    """Nested calls evaluate once before the containing call; occurrences retain distinct spans."""
    result = direct('Again();Again(); Create()->build(Outer(Inner())); return Last(); Never();')
    assert names(result) == ['Again', 'Again', 'Create', 'Inner', 'Outer', 'build', 'Last']
    steps = result['methodFlow']['steps']
    assert [step['kind'] for step in steps] == ['call'] * 7 + ['return']
    assert steps[0]['site'] != steps[1]['site']
    assert result['events'][4]['arguments'][0]['expression'] == 'Inner()'
    assert not result['methodFlow']['unsupported']


def test_if_elseif_conditional_context_and_empty_skip():
    """elseif tests are conditional on preceding false arms; true empty arms are explicit skip."""
    result = direct('if(Check()){Yes();}elseif(Other()){No();}else{Last();} if($x){}')
    check, yes, other, no, last = result['events']
    assert not check['conditional']
    assert yes['conditional'] and other['conditional'] and last['conditional']
    assert len(no['branchIds']) == len(last['branchIds']) == 2
    branch = result['methodFlow']['steps'][1]
    assert [step['kind'] for step in branch['arms'][1]['steps']] == ['call', 'branch']
    assert all(arm['emptyReason'] == 'skip' for arm in result['methodFlow']['steps'][-1]['arms'])
    arm_ids = {arm['id'] for branch in result['controlFlow']['branches'] for arm in branch['arms']}
    assert all(set(event['branchIds']) <= arm_ids for event in result['events'])


@pytest.mark.parametrize('operator', ['&&', '||', 'and', 'or', '??', '??='])
def test_short_circuits_keep_rhs_conditional(operator):
    """An RHS invocation is never an unconditional sibling of its LHS."""
    left = '$value' if operator == '??=' else 'First()'
    result = direct(f'{left} {operator} Last();')
    last = result['events'][-1]
    assert last['callee'] == 'Last' and last['conditional'] and last['branchIds']
    branch = result['methodFlow']['steps'][-1]
    assert branch['kind'] == 'branch' and branch['controlKind'] == operator
    if operator.startswith('??'):
        assert branch['arms'][1]['steps'][0]['reuseResult']
    else:
        assert branch['arms'][1]['emptyReason'] == 'skip'


def test_ternary_elvis_literals_and_nullsafe():
    """Value-only alternatives stay visible and shorthand condition is not called twice."""
    result = direct('$a=Flag()?1:2; $b=Read()?:Fallback(); $x?->load(Arg());')
    assert names(result) == ['Flag', 'Read', 'Fallback', 'Arg', 'load']
    branches = [step for step in result['methodFlow']['steps'] if step['kind'] == 'branch']
    assert [[step['expression'] for step in arm['steps']] for arm in branches[0]['arms']] == [['1'], ['2']]
    assert branches[1]['arms'][0]['steps'][0]['reuseResult']
    assert all(event['conditional'] for event in result['events'][-3:])
    assert branches[2]['arms'][1]['steps'][0]['expression'] == 'null'


def test_loops_jumps_initialization_condition_and_update():
    """Foreach collection is outside the body; continue and break remain actual jump steps."""
    result = direct('for(Init();Check();Update()){if(Skip())continue;Work();if(Done())break;} '
                    'foreach(Load() as $k=>$v){UseIt($v);} while(Test()){More();} '
                    'do{Outer(Inner());}while(Again()); for(;;){continue;}')
    assert names(result) == ['Init', 'Check', 'Skip', 'Work', 'Done', 'Update', 'Load', 'UseIt', 'Test', 'More', 'Inner', 'Outer', 'Again']
    steps = result['methodFlow']['steps']
    loop = steps[1]
    assert loop['controlKind'] == 'for'
    assert loop['condition'][0]['label'] == 'Check()' and loop['update'][0]['label'] == 'Update()'
    assert loop['body'][1]['arms'][0]['steps'][0]['kind'] == 'continue'
    assert loop['body'][-1]['arms'][0]['steps'][0]['kind'] == 'break'
    assert steps[2]['label'] == 'Load()' and steps[3]['controlKind'] == 'foreach'
    assert steps[5]['controlKind'] == 'do' and steps[5]['conditionPosition'] == 'after-body'
    assert not steps[-1]['canExit']


def test_switch_shared_labels_and_match_no_default():
    """Switch groups empty labels; unmatched match throws instead of silently skipping."""
    result = direct('switch(Code()){case 1:case 2:One();break;default:Other();} '
                    '$x=match(Choose()){1,2=>First(),3=>42};')
    assert names(result) == ['Code', 'One', 'Other', 'Choose', 'First']
    branches = [step for step in result['methodFlow']['steps'] if step['kind'] == 'branch']
    assert branches[0]['arms'][0]['label'] == '1 / 2'
    assert branches[0]['arms'][0]['steps'][-1]['kind'] == 'break'
    assert branches[1]['arms'][1]['steps'][0]['expression'] == '42'
    assert branches[1]['arms'][-1]['steps'][0]['implicitThrow']
    assert not result['methodFlow']['unsupported']


def test_constant_and_signed_case_labels_and_multiple_loop_expressions():
    """Source constants stay named; comma-separated headers retain distinct evaluation phases."""
    result = direct('switch(Code()){case -1:One();break;case Status::OK:Two();break;case READY:Three();break;} '
                    'for(Init(),SecondInit();Check(),SecondCheck();Next(),SecondNext()){Body();}')
    assert names(result) == ['Code', 'One', 'Two', 'Three', 'Init', 'SecondInit', 'Check', 'SecondCheck', 'Body', 'Next', 'SecondNext']
    branch = result['methodFlow']['steps'][1]
    assert [arm['label'] for arm in branch['arms']] == ['-1', 'Status::OK', 'READY', 'нет совпадения']
    assert not result['methodFlow']['unsupported']


@pytest.mark.parametrize('body,expected', [
    ('switch(Code()){case 1:One();case 2:Two();}', 'fallthrough'),
    ('switch(Code()){case Dynamic():One();break;}', 'метки'),
    ('$x=match(Code()){Dynamic()=>One(),default=>Two()};', 'метки'),
    ('try{Work();return Result();}finally{Clean();}', 'try_statement'),
    ('$a?->one()->two(Arg());', 'nullsafe'),
    ('$a?->one()?->two();', 'nullsafe'),
    ('while($x){break 2;}', 'многоуровневый'),
    ('goto somewhere; somewhere: Work();', 'goto_statement'),
    ('switch(Code()){case 1:continue;default:Work();}', 'continue внутри switch'),
    ('foreach(Items() as $targets[Key()]) Work();', 'foreach-присваивание'),
])
def test_unsupported_paths_are_visible_not_linear(body, expected):
    """Unsupported bodies cannot advertise their nested calls as unconditional execution."""
    result = direct(body)
    assert any(expected in reason for reason in result['methodFlow']['unsupported'])
    assert any(step['kind'] == 'unknown' and step['incomplete'] for step in flatten(result['methodFlow']['steps']))
    assert all(name not in names(result) for name in ['One', 'Two', 'Work', 'Clean', 'Arg'])


def test_closures_local_functions_and_generators_are_deferred():
    """Closure bodies and generator invocation are never flattened into the outer method."""
    result = direct('$f=fn($x)=>Deferred($x); $g=function(){ Hidden(); yield Value(); }; '
                    'function local(){ LocalBody(); } Now();')
    assert names(result) == ['Now']
    assert len([step for step in flatten(result['methodFlow']['steps']) if step.get('deferred')]) == 3
    generator = direct('Before(); yield Key()=>Value(); After(); return Last();')
    assert not generator['events']
    assert len(generator['methodFlow']['steps']) == 1
    assert 'generator' in generator['methodFlow']['unsupported'][0]


def test_include_require_dynamic_calls_and_throw():
    """Include operands are source facts; paths and dynamic call targets are never loaded."""
    result = direct('include Path(); require_once "/nonexistent.php"; $fn(Arg()); '
                    '$obj->{$method}(Other()); throw new Boom(Inner()); Never();')
    assert names(result) == ['Path', 'Arg', '$fn', 'Other', '$method', 'Inner', 'Boom']
    dynamic = [event for event in result['events'] if event['callee'].startswith('$')]
    assert all(event['confidence'] == 'unresolved' and not event['targetNodeIds'] for event in dynamic)
    steps = result['methodFlow']['steps']
    assert steps[-1]['kind'] == 'throw'
    assert len([step for step in steps if step['kind'] == 'unknown']) == 2
    assert any('include/require' in reason for reason in result['unresolved'])
    assert any('динамические' in reason for reason in result['unresolved'])


def test_selection_rejects_stale_line_ambiguous_owner_and_parse_errors():
    """Even a unique name cannot select a neighboring declaration outside its source range."""
    assert direct('Yes();', line=200)['sourceStatus'] == 'unresolved'
    source = {'text': '<?php\nclass A {function run(){AOnly();}}\nclass B {function run(){BOnly();}}'}
    node = {'id': 'raw', 'label': 'run'}
    assert analyze(source, node, max_events=160)['sourceStatus'] == 'unresolved'
    chosen = analyze(source, {**node, 'callableFacts': {'declaringType': 'B'}}, max_events=160)
    assert names(chosen) == ['BOnly']
    assert analyze(source, {**node, 'line': 2, 'callableFacts': {'declaringType': 'B'}}, max_events=160)['sourceStatus'] == 'unresolved'
    removed_owner = {'text': '<?php function run(){WrongModule();}'}
    assert analyze(removed_owner, {**node, 'callableFacts': {'declaringType': 'B'}}, max_events=160)['sourceStatus'] == 'unresolved'
    invalid = direct('if( { Bad();')
    assert invalid['sourceStatus'] == 'unresolved' and not invalid['events']
    assert invalid['methodFlow']['status'] == 'unavailable'


def test_declaration_only_and_comment_ownership():
    """An interface signature is supported without claiming an extracted body or inherited docs."""
    source = {'text': '<?php /** Class docs. */ interface C { public function run(int $n): string; }'}
    result = analyze(source, {'label': 'run', 'line': 1}, max_events=20)
    assert result['sourceStatus'] == 'parsed' and result['methodFlow']['status'] == 'declaration-only'
    assert result['documentation']['summary'] == ''
    assert result['typeContext']['documentation']['summary'] == 'Class docs.'


def test_budgets_events_steps_depth_and_ast(monkeypatch):
    """Every cap is bounded and visibly incomplete; call steps never point at omitted events."""
    limited = direct('A(B());C();D();', max_events=1)
    assert names(limited) == ['B'] and limited['eventsTruncated']
    assert len([step for step in flatten(limited['methodFlow']['steps']) if step['kind'] == 'call']) == 1
    assert any('events' in reason for reason in limited['unresolved'])
    many = direct('A();' * 300, max_events=1000)
    assert many['methodFlow']['truncated'] and len(flatten(many['methodFlow']['steps'])) <= 180
    deep = direct('if($x){' * 35 + 'Hidden();' + '}' * 35)
    assert deep['methodFlow']['truncated'] and 'Hidden' not in names(deep)
    ast_large = direct('A();' * 5000, max_events=1000)
    assert ast_large['sourceStatus'] == 'unavailable' and ast_large['methodFlow']['truncated']
    assert not ast_large['events']
    monkeypatch.setattr('atlas.flow_languages.php_support.source.MAX_AST_NODES', 10)
    ast = direct('A();B();')
    assert ast['sourceStatus'] == 'unavailable' and ast['methodFlow']['truncated'] and not ast['events']


def test_utf8_byte_coordinates_are_exact():
    """Published positions use Tree-sitter byte columns, not Python Unicode indices."""
    body = '$s="Привет"; Wrap(Inner());'
    source = '<?php function run(){' + body + '}'
    result = direct(body)
    assert names(result) == ['Inner', 'Wrap']
    raw = source.encode()
    for event in result['events']:
        expression = raw[event['column'] - 1:event['endColumn'] - 1].decode()
        assert expression == event['sourceExpression']


def test_callable_creation_does_not_invoke_and_literal_property_is_visible():
    """First-class callable syntax retains its deferred boundary; nullable property has two values."""
    callable_result = direct('Foo(...); $obj->Bar(...);')
    assert not callable_result['events']
    assert all(step['kind'] == 'unknown' for step in callable_result['methodFlow']['steps'])
    assert any('first-class callable' in issue for issue in callable_result['unresolved'])
    nullable = direct('$obj?->property;')
    branch = nullable['methodFlow']['steps'][0]
    assert [[step['expression'] for step in arm['steps']] for arm in branch['arms']] == [['$obj?->property'], ['null']]


def test_unreachable_case_statements_and_throw_operands_have_no_fallthrough():
    """An explicit terminal inside a case or expression cannot lead into sibling calls."""
    result = direct('switch(Code()){case 1:return Result(); Never();break;default:Other();}')
    assert names(result) == ['Code', 'Result', 'Other']
    arm = result['methodFlow']['steps'][1]['arms'][0]
    assert arm['steps'][-1]['kind'] == 'return'
    for body in ['$x=throw new Boom(); Never();', 'return throw new Boom(); Never();',
                 'if(throw new Boom()){Never();}', 'Wrap(throw new Boom(), Never());']:
        terminal = direct(body)
        assert names(terminal) == ['Boom']
        assert terminal['methodFlow']['steps'][-1]['kind'] == 'throw'
    coalescing = direct('$x ??= throw new Boom();')
    evaluated_arm = coalescing['methodFlow']['steps'][0]['arms'][0]
    assert [step['kind'] for step in evaluated_arm['steps']] == ['call', 'throw']


def test_missing_grammar_is_explicit(monkeypatch):
    """An unavailable local grammar never falls back to fabricated source symbols."""
    import sys
    monkeypatch.setitem(sys.modules, 'tree_sitter_php', None)
    result = direct('Hidden();')
    assert result['sourceStatus'] == 'unavailable'
    assert not result['events'] and not result['methodFlow']['steps']
    assert 'грамматика' in result['unresolved'][0]
