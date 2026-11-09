"""Real C# AST verifies local evaluation order and control ownership without running code."""
from atlas.node_description import build_node_description
from tests.test_interface_dispatch import fixture


def describe(tmp_path, body):
    """Use the production analyzer and an exact source fixture method."""
    graph, _ = fixture(tmp_path, 'class C { async Task Run(){' + body + '} }')
    node = next(n for n in graph['nodes'] if n['label'] == '.Run()')
    return build_node_description(tmp_path, graph, node['id'], 's')


def test_nested_arguments_await_and_repeated_calls(tmp_path):
    result = describe(tmp_path, 'Prepare(); Prepare(); return Ok(await Calculate(Arg()));')
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'call', 'call', 'call', 'await', 'call', 'return']
    assert [s['label'] for s in steps if s['kind'] == 'call'] == ['Prepare()', 'Prepare()', 'Arg()', 'Calculate()', 'Ok()']
    assert steps[3]['directAwait']
    assert steps[0]['site'] != steps[1]['site']
    assert not result['methodFlow']['truncated']


def test_branches_loops_jumps_and_deferred_lambda(tmp_path):
    result = describe(tmp_path, 'Before(); if(Check()){Yes();}else{No();} foreach(var x in Load()){if(Skip(x))continue; Work(x); if(Done())break;} After(); Action f=()=>Deferred();')
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call','call','branch','call','loop','call','operation']
    choice = steps[2]
    assert [[s['label'] for s in arm['steps']] for arm in choice['arms']] == [['Yes()'],['No()']]
    assert any(s['kind']=='branch' for s in steps[4]['body'])
    assert all('Deferred' not in s['label'] for s in steps if s['kind'] == 'call')
    assert steps[-1]['operationKind'] == 'declaration'


def test_for_do_switch_and_limit(tmp_path, monkeypatch):
    result=describe(tmp_path,'for(Init();Check();Update()){Work();} do{Work();}while(Check()); switch(Code()){case 1:One();break;default:Other();break;} End();')
    flow=result['methodFlow'];steps=flow['steps']
    assert steps[1]['controlKind']=='for' and steps[1]['update'][0]['label']=='Update()'
    assert steps[2]['controlKind']=='do' and steps[2]['body'][0]['label']=='Work()'
    assert steps[4]['controlKind']=='switch' and len(steps[4]['arms'])==2
    monkeypatch.setattr('atlas.method_flow.MAX_STEPS',2)
    limited=describe(tmp_path,'First();Second();Third();')['methodFlow']
    assert limited['truncated'] and len(limited['steps'])==2


def test_optional_switch_and_shared_cases(tmp_path):
    flow = describe(tmp_path, 'x?.Run(Arg()); var v=x switch {1=>One(),_=>Other()}; switch(x){case 1:case 2:Work();break;} After();')['methodFlow']
    optional, expression, declaration, statement, after = flow['steps']
    assert declaration['label'] == 'var v=x switch {1=>One(),_=>Other()};'
    assert optional['controlKind'] == '?.'
    assert [s['label'] for s in optional['arms'][0]['steps']] == ['Arg()', 'x?.Run()']
    assert optional['arms'][1]['steps'] == []
    assert [[s['label'] for s in a['steps']] for a in expression['arms']] == [['One()'], ['Other()']]
    assert statement['arms'][0]['label'] == 'case 1 / case 2'
    assert statement['arms'][0]['steps'][0]['label'] == 'Work()'
    assert statement['arms'][1]['label'] == 'нет совпадения'
    assert after['label'] == 'After()'


def test_chained_calls_and_multiple_for_updates(tmp_path):
    steps = describe(tmp_path, 'Create().Build().Send(); for(A(),B();C();D(),E()){Work();} for(;;){continue;}')['methodFlow']['steps']
    assert [s['label'] for s in steps[:3]] == ['Create()', 'Create().Build()', 'Create().Build().Send()']
    assert len({tuple(s['site'].values()) for s in steps[:3]}) == 3
    assert [s['label'] for s in steps[3:5]] == ['A()', 'B()']
    assert [s['label'] for s in steps[5]['update']] == ['D()', 'E()']
    assert steps[-1]['canExit'] is False


def test_unknown_finally_and_optional_chain_are_explicit(tmp_path):
    flow = describe(tmp_path, 'try { return Work(); } finally { Clean(); } x?.One().Two();')['methodFlow']
    assert flow['steps'][0]['incomplete']
    assert any('finally' in reason for reason in flow['unsupported'])
    assert flow['steps'][-1]['kind'] == 'unknown'


def test_for_continue_retains_increment_and_assignment(tmp_path):
    steps = describe(tmp_path, 'for(var i=0;i<10;i++){if(Skip())continue; result=Work();} return result;')['methodFlow']['steps']
    assert steps[0]['label'] == 'var i=0'
    assert steps[1]['update'][0]['kind'] == 'operation'
    assert steps[1]['update'][0]['label'] == 'i++'
    assert [s['kind'] for s in steps[1]['body']] == ['call', 'branch', 'call', 'operation']
    assert steps[1]['body'][-1]['label'] == 'result=Work()'


def test_literal_property_and_null_outcomes_are_explicit(tmp_path):
    """A value-only arm must not become a visually empty branch."""
    steps = describe(tmp_path, 'var a=ready ? 10 : fallback; var b=value ?? 20; return result?.Security;')['methodFlow']['steps']
    ternary, first_declaration, coalesce, second_declaration, optional, terminal = steps
    assert first_declaration['variable'] == 'a' and second_declaration['variable'] == 'b'
    assert [[s['expression'] for s in a['steps']] for a in ternary['arms']] == [['10'], ['fallback']]
    assert [[s['expression'] for s in a['steps']] for a in coalesce['arms']] == [['20'], ['value']]
    assert coalesce['arms'][1]['steps'][0]['reuseResult']
    assert [[s['expression'] for s in a['steps']] for a in optional['arms']] == [['result.Security'], ['null']]
    assert all(s['line'] == terminal['line'] for a in optional['arms'] for s in a['steps'])
    assert terminal['kind'] == 'return'


def test_value_selection_keeps_calls_single_and_short_circuit_skips(tmp_path):
    """Selection of an already computed result does not insert another invocation."""
    flow = describe(tmp_path, 'var a=GetValue() ?? GetFallback(); var b=ok && Check(); var c=ok || Check(); return GetValue()?.Security;')['methodFlow']
    def calls(steps):
        result = []
        for step in steps:
            if step['kind'] == 'call':
                result.append(step['label'])
            for arm in step.get('arms', []):
                result.extend(calls(arm['steps']))
        return result
    assert calls(flow['steps']) == ['GetValue()', 'GetFallback()', 'Check()', 'Check()', 'GetValue()']
    branches = [s for s in flow['steps'] if s['kind'] == 'branch']
    assert branches[1]['arms'][1]['steps'] == []
    assert branches[1]['arms'][1]['emptyReason'] == 'skip'
    assert 'ложна' in branches[1]['arms'][1]['label']
    assert 'истинна' in branches[2]['arms'][1]['label']
    assert branches[3]['arms'][0]['steps'][0]['expression'] == 'GetValue().Security'
    assert not flow['unsupported']


def test_switch_literals_and_unrepresented_statements(tmp_path):
    """Actual source that the extractor omitted cannot be advertised as an empty branch."""
    flow = describe(tmp_path, 'var a=x switch {1=>"one",_=>"other"}; if(ok){} if(ok){var value=1;} while(ok){var value=2;}')['methodFlow']
    switch, declaration, empty, initialized, loop = flow['steps']
    assert [[s['expression'] for s in a['steps']] for a in switch['arms']] == [['"one"'], ['"other"']]
    assert all(a['emptyReason'] == 'skip' for a in empty['arms'])
    assert declaration['operationKind'] == 'declaration'
    assert initialized['arms'][0]['steps'][0]['label'] == 'var value=1;'
    assert loop['body'][0]['label'] == 'var value=2;'
    assert loop['bodyEmptyReason'] is None
    assert not flow['unsupported']


def test_direct_call_initializers_retain_the_source_statement(tmp_path):
    """Code rows retain variable names without treating every nested call as a declaration."""
    steps = describe(tmp_path, 'var candidateIsShort=Check(candidate); var currentIsShort=await CheckAsync(current); var a=Wrap(Inner()); if(Check()){Work();}')['methodFlow']['steps']
    calls = [s for s in steps if s['kind'] == 'call']
    assert calls[0]['sourceStatement'] == 'var candidateIsShort=Check(candidate);'
    assert calls[1]['sourceStatement'] == 'var currentIsShort=await CheckAsync(current);'
    assert calls[2]['label'] == 'Inner()' and calls[2]['sourceStatement'] == ''
    assert calls[3]['sourceStatement'] == 'var a=Wrap(Inner());'
    assert calls[4]['sourceStatement'] == ''


def test_local_setup_literal_property_multiple_and_conditional_initializers(tmp_path):
    """A reader sees every binding once, in evaluation order, including value-only setup."""
    steps = describe(tmp_path, 'PrioritizedSecurity? result=null; var ticker=security.Ticker; int a=1,b=Read(); var selected=ok ? First() : Second(); return selected;')['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['operation', 'operation', 'operation', 'call', 'operation', 'branch', 'operation', 'return']
    assert [s['label'] for s in steps[:3]] == ['PrioritizedSecurity? result=null;', 'var ticker=security.Ticker;', 'int a=1']
    assert steps[4]['label'] == 'int b=Read()'
    assert steps[6]['variable'] == 'selected'
    assert [[s['label'] for s in a['steps']] for a in steps[5]['arms']] == [['First()'], ['Second()']]


def test_direct_initializers_do_not_duplicate_and_using_setup_is_visible(tmp_path):
    """Initializer call rows retain names, while for/using declarations have explicit ownership."""
    steps = describe(tmp_path, 'var x=Read(); var y=await ReadAsync(); using(var stream=Open()){Use(stream);} using var other=Open(); for(var i=0,j=Seed();i<3;i++){Work();}')['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'call', 'await', 'call', 'operation', 'call', 'call', 'operation', 'call', 'operation', 'loop']
    assert steps[0]['sourceStatement'] == 'var x=Read();'
    assert steps[1]['sourceStatement'] == 'var y=await ReadAsync();'
    assert steps[4]['label'] == 'var stream=Open()'
    assert steps[6]['sourceStatement'] == 'using var other=Open();'
    assert [s['label'] for s in steps[7:10]] == ['var i=0', 'Seed()', 'var j=Seed()']


def test_short_circuit_consuming_condition_reuses_value_without_new_calls(tmp_path):
    """Compound conditions evaluate once; the final if/loop consumes the calculated result."""
    steps = describe(tmp_path, 'if(!(First() && Second()) || Third()){Work();} if(Simple()){Work();} while(Check() && More()){Work();}')['methodFlow']['steps']
    computed, choice = steps[2:4]
    assert computed['controlKind'] == '||'
    assert choice['controlKind'] == 'if' and choice['conditionResult'] == 'reuse'
    assert choice['conditionExpression'] == choice['label'] == '!(First() && Second()) || Third()'
    assert 'conditionResult' not in steps[5]
    assert steps[-1]['conditionResult'] == 'reuse'
    def calls(items):
        """Collect real calls from each lexical branch and loop condition without flattening execution."""
        result = []
        for item in items:
            if item['kind'] == 'call':
                result.append(item['label'])
            for arm in item.get('arms', []):
                result.extend(calls(arm['steps']))
            result.extend(calls(item.get('condition', [])))
            result.extend(calls(item.get('body', [])))
        return result
    assert calls(steps) == ['First()', 'Second()', 'Third()', 'Work()', 'Simple()', 'Work()', 'Check()', 'More()', 'Work()']


def test_deconstruction_and_uninitialized_local_preserve_bindings_and_budget(tmp_path, monkeypatch):
    """Tuple binding syntax is not mistaken for its initializer or omitted from the shared budget."""
    steps = describe(tmp_path, 'var (a,b)=Read(); int unused; var selected=ok ? 1 : 2;')['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'operation', 'branch', 'operation']
    assert steps[0]['sourceStatement'] == 'var (a,b)=Read();'
    assert steps[1]['variable'] == 'unused' and steps[1]['initializer'] == ''
    assert steps[3]['variable'] == 'selected'
    monkeypatch.setattr('atlas.method_flow.MAX_STEPS', 2)
    flow = describe(tmp_path, 'int one=1; int two=2; int three=3;')['methodFlow']
    assert flow['truncated'] and [s['variable'] for s in flow['steps']] == ['one', 'two']


def test_expression_bodied_methods_have_value_normal_and_exceptional_exits(tmp_path):
    """Arrow syntax still exposes calls followed by its actual value, normal exit or throw."""
    graph, _ = fixture(tmp_path, '''class C {
      int Literal()=>42;
      int Value()=>Read();
      void Work()=>Use();
      async Task Wait()=>await UseAsync();
      async Task<int> AwaitValue()=>await ReadAsync();
      int Fail()=>throw new InvalidOperationException();
    }''')
    flows = {}
    for node in graph['nodes']:
        if node['kind'] == 'method':
            flows[node['label']] = build_node_description(tmp_path, graph, node['id'], 's')['methodFlow']
    literal = flows['.Literal()']['steps']
    assert len(literal) == 1 and literal[0]['kind'] == 'return' and literal[0]['label'] == 'return 42;'
    assert literal[0]['implicitReturn'] and literal[0]['returnsValue']
    assert not literal[0]['reuseExpressionResult']
    assert literal[0]['sourceStatement'] == '=>42'
    assert [s['kind'] for s in flows['.Value()']['steps']] == ['call', 'return']
    value_exit = flows['.Value()']['steps'][-1]
    assert value_exit['reuseExpressionResult'] and value_exit['label'] == 'Вернуть результат выражения'
    assert value_exit['expression'] == 'Read()' and value_exit['sourceStatement'] == '=>Read()'
    normal = flows['.Work()']['steps']
    assert [s['kind'] for s in normal] == ['call', 'return']
    assert normal[-1]['label'] == 'Завершить метод' and not normal[-1]['returnsValue']
    for label, value in [('.Wait()', False), ('.AwaitValue()', True)]:
        steps = flows[label]['steps']
        assert [s['kind'] for s in steps] == ['call', 'await', 'return']
        assert steps[-1]['returnsValue'] is value
        assert steps[-1]['reuseExpressionResult'] is value
    assert [s['kind'] for s in flows['.Fail()']['steps']] == ['throw']
    assert all(not flow['truncated'] for flow in flows.values())


def test_throw_expression_in_value_selection_keeps_exceptional_exit(tmp_path):
    """A throwing value arm cannot continue as an ordinary produced value."""
    steps = describe(tmp_path, 'var selected=ok ? Read() : throw Failure(); return selected;')['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['branch', 'operation', 'return']
    assert [s['kind'] for s in steps[0]['arms'][1]['steps']] == ['call', 'throw']


def test_arrow_conditional_ref_and_unbound_async_types_preserve_source_contract(tmp_path):
    """Implicit exits reuse computed values/references; unresolved async shapes remain unknown."""
    graph, _ = fixture(tmp_path, '''using TaskAlias=System.Threading.Tasks.Task;
    class C {
      int Select()=>ok ? Read() : throw Failure();
      ref int Field()=>ref field;
      ref int Locate()=>ref Find();
      async TaskAlias Alias()=>await Work();
      async CustomAwaitable Custom()=>await Work();
    }''')
    flows = {}
    for node in graph['nodes']:
        if node['kind'] == 'method':
            flows[node['label']] = build_node_description(tmp_path, graph, node['id'], 's')['methodFlow']
    select = flows['.Select()']['steps']
    assert [s['kind'] for s in select] == ['branch', 'return']
    assert [s['kind'] for s in select[0]['arms'][1]['steps']] == ['call', 'throw']
    assert select[-1]['reuseExpressionResult'] and select[-1]['label'] == 'Вернуть результат выражения'
    field = flows['.Field()']['steps'][-1]
    assert field['returnsReference'] and field['label'] == 'return ref field;'
    locate = flows['.Locate()']['steps']
    assert [s['kind'] for s in locate] == ['call', 'return']
    assert locate[-1]['returnsReference'] and locate[-1]['reuseExpressionResult']
    assert locate[-1]['label'] == 'Вернуть ссылку на результат выражения'
    for label in ['.Alias()', '.Custom()']:
        flow = flows[label]
        assert [s['kind'] for s in flow['steps']] == ['call', 'await', 'return']
        assert flow['steps'][-1]['returnsValue'] is None
        assert flow['steps'][-1]['label'] == 'Завершить метод'
        assert any('result-type binding' in limitation for limitation in flow['unsupported'])
    aliased_graph, _ = fixture(tmp_path, 'using Task=CustomAwaitable; class C { async Task Run()=>await Work(); }')
    node = next(n for n in aliased_graph['nodes'] if n['label'] == '.Run()')
    aliased = build_node_description(tmp_path, aliased_graph, node['id'], 's')['methodFlow']
    assert aliased['steps'][-1]['returnsValue'] is None
    assert any('result-type binding' in limitation for limitation in aliased['unsupported'])
