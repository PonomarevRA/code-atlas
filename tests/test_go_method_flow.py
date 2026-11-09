"""Go fixtures проверяются статически через реальные host/description entry points."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import builtins

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.go import analyze
from atlas.member_graph import enrich_member_graph
from atlas.node_description import build_node_description

FIXTURES = Path(__file__).parent / 'fixtures' / 'flow' / 'go'


def describe(tmp_path, body, *, max_events=160, label='Run()', line=2):
    """Проходит production safe reader и SourceAnalysis; код Go не исполняется."""
    source = 'package p\nfunc Run() {' + body + '}\n'
    path = tmp_path / 'main.go'
    path.write_text(source)
    node = {'id': 'raw-run', 'label': label, 'file': path.name, 'line': line}
    result = SourceAnalysis(tmp_path, 'go-test', 3, 1024 * 1024, max_events).describe(node)
    assert path.read_text() == source
    return result


def steps_in(steps):
    """Обходит дерево в том же статическом порядке condition/body/update/arms."""
    result = []
    for step in steps:
        result.append(step)
        for field in ('condition', 'body', 'update'):
            result.extend(steps_in(step.get(field, [])))
        for arm in step.get('arms', []):
            result.extend(steps_in(arm['steps']))
    return result


def calls(result):
    """Возвращает имена синтаксических событий без runtime-связывания."""
    return [event['callee'] for event in result['events']]


def test_real_member_graph_and_description_preserve_source_and_raw_ids(tmp_path):
    """Реальная Go member map дополняет file node и связывает только существующие raw IDs."""
    source = (FIXTURES / 'service.go').read_text()
    path = tmp_path / 'service.go'
    path.write_text(source)
    raw = {'nodes': [{'id': 'raw-file', 'kind': 'file', 'label': path.name, 'file': path.name, 'line': 1}],
           'edges': [], 'metadata': {'snapshotId': 'go-test'}}
    raw_before = deepcopy(raw)
    graph = enrich_member_graph(raw, tmp_path)
    assert raw == raw_before
    run = next(node for node in graph['nodes'] if node.get('label') == 'Run()')
    graph['nodes'].append({'id': 'raw-seed', 'label': 'Seed()', 'kind': 'external'})
    graph['edges'].append({'source': run['id'], 'target': 'raw-seed', 'relation': 'calls'})
    before = deepcopy(graph)
    result = build_node_description(tmp_path, graph, run['id'], 'go-test')
    assert result['sourceStatus'] == result['methodFlow']['status'] == 'parsed'
    assert result['analyzerVersion'] == 'go-method-flow-v1'
    assert result['nodeId'] == run['id']
    assert run['callableFacts']['controlFlowSupported'] is False
    assert result['callableFacts']['controlFlowSupported'] is True
    assert result['callableFacts']['controlFlowSourceKind'] == 'working-tree-ast'
    assert result['sourceHash'] == sha256(source.encode()).hexdigest()
    assert result['sourceByteHash'] and result['sourceReadAt']
    seed = next(event for event in result['events'] if event['callee'] == 'Seed')
    assert seed['targetNodeIds'] == ['raw-seed']
    assert all(set(event['targetNodeIds']) <= {node['id'] for node in graph['nodes']} for event in result['events'])
    assert result['parameters'] == [{'name': 'items', 'type': '[]int'}, {'name': 'ready', 'type': 'bool'}]
    assert result['returnType'] == 'int'
    assert result['documentation']['summary'].startswith('Run preserves')
    assert result['parameterFacts']['status'] == result['attributeFacts']['status'] == 'unavailable'
    assert result['readabilityRecommendations']['status'] == 'unavailable'
    assert not result['methodFlow']['truncated']
    assert not result['methodFlow']['unsupported'] or all('range iteration' in reason for reason in result['methodFlow']['unsupported'])
    assert graph == before and path.read_text() == source
    assert 'Seed' == calls(result)[0]
    assert calls(result)[-2:] == ['Finish', 'Wrap']
    flow_calls = [step for step in steps_in(result['methodFlow']['steps']) if step['kind'] == 'call']
    sites = lambda items: [(item['line'], item['column'], item['endLine'], item['endColumn']) for item in items]
    assert sites([step['site'] for step in flow_calls]) == sites(result['events'])
    assert len(set(sites(result['events']))) == len(result['events'])
    arms = {arm['id'] for branch in result['controlFlow']['branches'] for arm in branch['arms']}
    assert arms
    for item in result['events'] + result['controlFlow']['terminals']:
        assert set(item['branchIds']) <= arms
    for branch in result['controlFlow']['branches']:
        assert set(branch['parentArmIds']) <= arms


def test_nested_arguments_receiver_order_repeated_calls_and_utf8_sites(tmp_path):
    """Site указывает на полный UTF-8 вызов; вложенный аргумент не сортируется после внешнего."""
    result = describe(tmp_path, 's := "ёж"; Build().Wrap(Inner(Arg()), Last()); Again(); Again(); return Done()')
    assert calls(result) == ['Build', 'Arg', 'Inner', 'Last', 'Wrap', 'Again', 'Again', 'Done']
    assert [step['kind'] for step in result['methodFlow']['steps']] == ['statement'] + ['call'] * 7 + ['call', 'return']
    data = (tmp_path / 'main.go').read_bytes().splitlines()
    for event in result['events']:
        assert event['sourceExpression'] == data[event['line'] - 1][event['column'] - 1:event['endColumn'] - 1].decode()
    assert len({(event['column'], event['endColumn']) for event in result['events']}) == len(result['events'])


def test_initializer_short_circuit_else_if_and_empty_arms(tmp_path):
    """Правый операнд и else-if не становятся безусловными событиями."""
    result = describe(tmp_path, 'if v:=Init(); Left(v) && Right() {Yes()} else if Other(){No()}; if Empty(){}; x := A() || B()')
    assert calls(result) == ['Init', 'Left', 'Right', 'Yes', 'Other', 'No', 'Empty', 'A', 'B']
    events = {event['callee']: event for event in result['events']}
    assert not events['Left']['conditional'] and not events['Init']['conditional']
    assert all(events[name]['conditional'] and events[name]['branchIds'] for name in ('Right', 'Yes', 'Other', 'No', 'B'))
    branches = [step for step in steps_in(result['methodFlow']['steps']) if step['kind'] == 'branch']
    assert [step['controlKind'] for step in branches] == ['&&', 'if', 'if', 'if', '||']
    assert branches[0]['arms'][1]['emptyReason'] == 'skip'
    assert branches[1]['conditionResult'] == 'reuse'
    assert events['Yes']['branchIds'][-1] != events['Other']['branchIds'][-1]
    assert all(arm['emptyReason'] == 'skip' for arm in branches[3]['arms'])


def test_for_range_once_infinite_loop_and_jump_ownership(tmp_path):
    """Init/range остаются перед циклом; repeated condition/update сохраняют владельца."""
    result = describe(tmp_path, 'for i:=Init(); Test(); i=Next(){if Skip(){continue}; Work(); if Stop(){break}}; for _,v:=range Load(){Use(v)};for Ready(){Work()}; for {continue}')
    assert calls(result) == ['Init', 'Test', 'Skip', 'Work', 'Stop', 'Next', 'Load', 'Use', 'Ready', 'Work']
    steps = result['methodFlow']['steps']
    loops = [step for step in steps if step['kind'] == 'loop']
    assert [step['controlKind'] for step in loops] == ['for', 'foreach', 'while', 'while']
    assert loops[0]['condition'][0]['label'] == 'Test()'
    assert loops[0]['update'][0]['label'] == 'Next()'
    assert loops[-1]['canExit'] is False
    load = next(event for event in result['events'] if event['callee'] == 'Load')
    use = next(event for event in result['events'] if event['callee'] == 'Use')
    assert not load['conditional'] and use['conditional'] and use['branchIds']
    assert [step['kind'] for step in result['controlFlow']['terminals']] == ['continue', 'break', 'continue']
    assert all(step['branchIds'] for step in result['controlFlow']['terminals'])


def test_expression_and_type_switch_preserve_case_test_laziness(tmp_path):
    """Case tests прекращаются после совпадения; type-list не является набором вызовов."""
    result = describe(tmp_path, 'switch x:=Init(); Tag(x){default: Default();case A(),B(): One();break;case C(): Two()}; switch v:=Input().(type){case int,string:Typed(v);default:Unknown(v)}')
    assert calls(result) == ['Init', 'Tag', 'A', 'B', 'One', 'C', 'Two', 'Default', 'Input', 'Typed', 'Unknown']
    events = {event['callee']: event for event in result['events']}
    assert not events['A']['conditional']
    assert all(events[name]['conditional'] for name in ('B', 'C', 'One', 'Two', 'Default', 'Typed', 'Unknown'))
    branches = [step for step in steps_in(result['methodFlow']['steps']) if step['kind'] == 'branch']
    assert branches[0]['controlKind'] == 'case-test'
    assert branches[0]['arms'][1]['emptyReason'] == 'skip'
    assert any(step.get('switchKind') == 'type-switch' for step in branches)
    assert not result['methodFlow']['unsupported']


def test_defer_go_arguments_are_current_but_invocations_and_closures_are_not(tmp_path):
    """Отложенные вызовы не попадают в события внешнего метода; аргументы вычисляются сейчас."""
    result = describe(tmp_path, 'defer Factory()(Arg()); go Worker(SpawnArg()); callback:=func(){Hidden()}; go func(x int){Inside(x)}(Now()); After()')
    assert calls(result) == ['Factory', 'Arg', 'SpawnArg', 'Now', 'After']
    boundaries = [step for step in result['methodFlow']['steps'] if step.get('incomplete')]
    assert len(boundaries) == 3 and all(step['kind'] == 'statement' for step in boundaries)
    assert any('defer' in reason for reason in result['unresolved'])
    assert any('scheduling' in reason for reason in result['unresolved'])
    assert all(event['parallel'] is False and event['awaited'] is False for event in result['events'])


def test_generic_deferred_arguments_and_default_only_switch_are_not_lost(tmp_path):
    """Необычная форма grammar node не должна скрыть вычисляемые сейчас аргументы."""
    result = describe(tmp_path, 'defer F[int](Arg()); go F[string](Spawn());switch x {default: Default()}')
    assert calls(result) == ['Arg', 'Spawn', 'Default']
    branch = result['methodFlow']['steps'][-1]
    assert branch['kind'] == 'branch' and branch['arms'][0]['label'] == 'default'
    assert result['events'][-1]['branchIds'] == [branch['arms'][0]['id']]


@pytest.mark.parametrize('body,absent,reason', [
    ('select {case x:=<-Chan(): Use(x);case Sink()<-Value():Sent();default:Other()}; After()', ['Chan','Use','Sink','Value','Sent','Other'], 'select/channel'),
    ('return Wrap(<-Chan(), Next())', ['Wrap','Chan','Next'], 'receive'),
    ('ch <- Value()', ['Value'], 'channel'),
    ('switch Tag(){case 1:One();fallthrough;case 2:Two()}', ['Tag','One','Two'], 'fallthrough'),
    ('for v:=range MaybeArray(){Use(v)}', ['MaybeArray','Use'], 'type/arity'),
    ('L: for {break L}', [], 'labels'),
    ('goto L; L: Done()', ['Done'], 'goto'),
    ('(func(){Hidden()})()', ['Hidden'], 'immediately invoked'),
])
def test_opaque_boundaries_do_not_invent_calls(tmp_path, body, absent, reason):
    """Неизвестный путь виден исходным шагом и причиной; тела не становятся линейными."""
    result = describe(tmp_path, body)
    assert not set(absent) & set(calls(result))
    assert any(reason in message for message in result['unresolved'])
    assert any(step['kind'] == 'unknown' for step in steps_in(result['methodFlow']['steps']))


def test_panic_boundary_has_no_claimed_normal_completion(tmp_path):
    """Panic может быть затенён, поэтому unwind/recover остаются неизвестным выходом."""
    result = describe(tmp_path, 'panic(Err()); After()')
    assert calls(result)[:2] == ['Err', 'panic']
    assert result['methodFlow']['steps'][2]['kind'] == 'unknown'
    assert any('unwind' in reason for reason in result['unresolved'])


def test_generic_conversion_ambiguity_and_compile_time_types_are_explicit(tmp_path):
    """Грамматика не отличает generic call от conversion; типовые константы не runtime calls."""
    result = describe(tmp_path, 'var values [len([2]int{})]int; x:=Generic[int](Arg()); return x')
    assert calls(result) == ['Arg']
    assert any('conversion/generic' in reason for reason in result['unresolved'])
    assert any(step['kind'] == 'unknown' for step in result['methodFlow']['steps'])


def test_source_preview_and_argument_budgets_are_explicit(tmp_path):
    """Полный site сохраняется, а длинный текст и список аргументов помечаются сокращёнными."""
    result = describe(tmp_path, 'Wrap("' + 'x' * 500 + '");Many(' + ','.join('v' + str(i) for i in range(60)) + ')')
    event = result['events'][0]
    assert len(event['sourceExpression']) == 400 and event['sourceExpressionTruncated']
    assert event['arguments'][0]['expressionTruncated']
    assert result['methodFlow']['steps'][0]['sourcePreviewTruncated']
    assert len(result['events'][1]['arguments']) == 40 and result['events'][1]['argumentsTruncated']


def test_actual_ast_limit_and_many_case_depth_are_bounded(tmp_path):
    """Настоящие большие входы ограничены без изменения констант анализатора."""
    too_big = describe(tmp_path, 'A();' * 7000)
    assert too_big['sourceStatus'] == 'unavailable' and too_big['methodFlow']['truncated']
    assert not too_big['events']
    cases = 'switch x {' + ''.join('case ' + str(i) + ': Work();' for i in range(40)) + '}'
    result = describe(tmp_path, cases)
    assert result['methodFlow']['truncated']
    assert any('depth budget' in reason for reason in result['unresolved'])


def test_exact_receiver_identity_no_name_only_or_stale_line_fallback():
    """Одинаковые имена у разных receivers и устаревшая строка не выбирают соседа."""
    source = {'text': 'package p\ntype A struct{}\ntype B struct{}\nfunc (a *A) Run(){AOnly()}\nfunc (b *B) Run(){BOnly()}\n'}
    assert analyze(source, {'label':'Run()'}, max_events=10)['sourceStatus'] == 'unresolved'
    selected = analyze(source, {'label':'Run()', 'line':5, 'callableFacts':{'declaringType':'B'}}, max_events=10)
    assert calls(selected) == ['BOnly']
    assert analyze(source, {'label':'Run()', 'line':4, 'callableFacts':{'declaringType':'B'}}, max_events=10)['sourceStatus'] == 'unresolved'
    assert analyze(source, {'label':'Run()', 'line':3}, max_events=10)['sourceStatus'] == 'unresolved'


def test_single_method_stale_line_and_end_line_are_rejected():
    """Даже единственный метод должен совпасть с указанным исходным диапазоном."""
    source = {'text':'package p\nfunc Run(){Only()}'}
    assert analyze(source, {'label':'Run()', 'line':3}, max_events=10)['sourceStatus'] == 'unresolved'
    assert analyze(source, {'label':'Run()', 'line':2, 'endLine':3}, max_events=10)['sourceStatus'] == 'unresolved'


def test_parameters_own_comments_and_external_declaration():
    """Named/unnamed/grouped/variadic типы сохраняются буквально; doc класса не наследуется."""
    text = 'package p\n// Type docs\ntype T struct{}\n\n// Run own docs\nfunc Run(a,b int, values ...string) (x int, err error) {return}\nfunc External(int)\n'
    result = analyze({'text':text}, {'label':'Run()', 'line':6}, max_events=10)
    assert result['documentation']['summary'] == 'Run own docs'
    assert result['parameters'] == [{'name':'a','type':'int'},{'name':'b','type':'int'},{'name':'values','type':'...string'}]
    assert result['returnType'] == '(x int, err error)'
    external = analyze({'text':text}, {'label':'External()', 'line':7}, max_events=10)
    assert external['methodFlow']['status'] == 'declaration-only'
    assert external['documentation']['summary'] == ''
    assert external['parameters'] == [{'name':'','type':'int'}]


def test_parse_error_grammar_absence_and_event_budget(tmp_path, monkeypatch):
    """Ошибочный AST/нет грамматики не даёт частичного выдуманного дерева."""
    broken = analyze({'text':'package p\nfunc Run(){ if { Missing() }'}, {'label':'Run()','line':2}, max_events=10)
    assert broken['sourceStatus'] == 'unresolved' and not broken['events']
    limited = describe(tmp_path, 'A();B();C()', max_events=1)
    assert calls(limited) == ['A'] and limited['eventsTruncated']
    assert len([step for step in limited['methodFlow']['steps'] if step['kind']=='call']) == 3
    original = builtins.__import__
    def no_grammar(name, *args, **kwargs):
        """Эмулирует отсутствие установленной Go-грамматики без изменения среды."""
        if name == 'tree_sitter_go':
            raise ModuleNotFoundError(name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_grammar)
    missing = analyze({'text':'package p\nfunc Run(){}'}, {'label':'Run()','line':2}, max_events=1)
    assert missing['sourceStatus'] == 'unavailable' and not missing['events']
    assert 'unavailable' in missing['unresolved'][0]


def test_step_depth_and_ast_budgets_are_visible(tmp_path, monkeypatch):
    """Все три бюджета обрывают обход с видимой причиной и bounded результатом."""
    limited = describe(tmp_path, 'A();' * 250)
    assert len(steps_in(limited['methodFlow']['steps'])) <= 180
    assert limited['methodFlow']['truncated'] and limited['controlFlow']['truncated']
    nested = describe(tmp_path, 'if ok {' * 40 + 'Hidden()' + '}' * 40)
    assert nested['methodFlow']['truncated'] and 'Hidden' not in calls(nested)
    monkeypatch.setattr('atlas.flow_languages.go.MAX_AST_NODES', 10)
    ast_limited = describe(tmp_path, 'A();B();C();D()')
    assert ast_limited['sourceStatus'] == 'unavailable'
    assert ast_limited['methodFlow']['truncated'] and not ast_limited['events']
