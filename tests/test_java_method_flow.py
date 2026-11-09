"""Java source fixtures verify static boundaries through the production description API."""
from copy import deepcopy
from pathlib import Path

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.java import analyze
from atlas.node_description import build_node_description

FIXTURE = Path(__file__).parent / 'fixtures/flow/java/FlowCases.java'


def direct(body, name='run', *, max_events=160):
    """Parse Java text in memory; never compile or execute the fixture."""
    return analyze({'text': 'class C { void ' + name + '(){' + body + '} }'},
                   {'id': 'raw:run', 'label': '.' + name + '()', 'line': 1}, max_events=max_events)


def flatten(steps):
    """Collect nested tree steps while retaining each occurrence only once."""
    result = []
    for step in steps:
        result.append(step)
        for arm in step.get('arms', []):
            result.extend(flatten(arm['steps']))
        for field in ('condition', 'body', 'update'):
            result.extend(flatten(step.get(field, [])))
    return result


def event_names(result):
    """Return lexical evaluation order, independently of source-start coordinates."""
    return [e['callee'] for e in result['events']]


def production(tmp_path, name):
    """Use exact fixture source, raw graph IDs and real safe-reader provenance."""
    content = FIXTURE.read_text()
    (tmp_path / 'FlowCases.java').write_text(content)
    marker = ('    FlowCases(int' if name == 'FlowCases' else ' ' + name + '(')
    line = next(i for i, text in enumerate(content.splitlines(), 1) if marker in text)
    node = {'id': 'raw:java:' + name, 'label': '.' + name + '()', 'line': line,
            'file': 'FlowCases.java', 'kind': 'symbol',
            'memberFacts': {'language': 'java', 'name': name, 'kind': 'constructor' if name == 'FlowCases' else 'method'}}
    graph = {'nodes': [node], 'edges': []}
    return graph, node, content


def test_production_describe_and_description_preserve_source_graph_and_ids(tmp_path):
    """Verify production describe and description preserve source graph and ids."""
    graph, node, content = production(tmp_path, 'run')
    graph['nodes'].append({'id': 'raw:target:send', 'label': '.send()', 'file': 'FlowCases.java'})
    graph['edges'].append({'source': node['id'], 'target': 'raw:target:send', 'relation': 'calls'})
    before = deepcopy(graph)
    analysis = SourceAnalysis(tmp_path, 'snapshot:java', 5, 1024 * 1024, 160)
    description = analysis.describe(node)
    result = build_node_description(tmp_path, graph, node['id'], 'snapshot:java')
    assert description['sourceStatus'] == result['sourceStatus'] == 'parsed'
    assert result['nodeId'] == node['id'] and result['snapshotId'] == 'snapshot:java'
    assert graph == before and (tmp_path / 'FlowCases.java').read_text() == content
    assert result['sourceHash'] and result['sourceByteHash'] and result['sourceReadAt']
    assert result['documentation']['summary'] == 'Evaluate arguments before sending a result.'
    assert result['parameters'] == [{'name': 'value', 'type': 'int'}, {'name': 'names', 'type': 'String...'}]
    assert result['returnType'] == 'int'
    assert result['parameterFacts']['status'] == result['attributeFacts']['status'] == 'unavailable'
    send = next(e for e in result['events'] if e['callee'] == 'send')
    assert send['targetNodeIds'] == ['raw:target:send']
    assert all(target in {n['id'] for n in graph['nodes']} for e in result['events'] for target in e['targetNodeIds'])
    calls = [s for s in flatten(result['methodFlow']['steps']) if s['kind'] == 'call']
    assert len(calls) == len(result['events'])
    assert {tuple(s['site'].values()) for s in calls} == {
        tuple(e[k] for k in ('line', 'column', 'endLine', 'endColumn')) for e in result['events']}


def test_exact_nested_argument_order_and_short_circuit_ownership(tmp_path):
    """Verify exact nested argument order and short circuit ownership."""
    graph, node, _ = production(tmp_path, 'run')
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert event_names(result) == ['prepare', 'ready', 'valid', 'first', 'inner', 'second', 'send', 'reason', 'Failure']
    assert [e['conditional'] for e in result['events']] == [False, False] + [True] * 7
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'call', 'branch', 'branch']
    assert steps[2]['controlKind'] == '&&' and steps[2]['arms'][1]['emptyReason'] == 'skip'
    assert [[s['kind'] for s in a['steps']] for a in steps[3]['arms']] == [
        ['call', 'call', 'call', 'call', 'return'], ['call', 'call', 'throw']]
    arm_ids = {a['id'] for b in result['controlFlow']['branches'] for a in b['arms']}
    assert all(set(e['branchIds']) <= arm_ids for e in result['events'])


def test_constructor_chain_in_production(tmp_path):
    """Verify constructor chain in production."""
    graph, node, _ = production(tmp_path, 'FlowCases')
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert result['kind'] == 'constructor'
    assert event_names(result) == ['seed', 'this', 'created']
    assert [s['kind'] for s in result['methodFlow']['steps']] == ['call'] * 3
    assert result['documentation']['summary'] == ''


def test_loop_header_body_update_and_do_order(tmp_path):
    """Verify loop header body update and do order."""
    graph, node, _ = production(tmp_path, 'loops')
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert event_names(result) == ['start', 'check', 'skip', 'use', 'done', 'next', 'tick',
                                  'load', 'use', 'available', 'poll', 'once', 'again', 'after']
    steps = result['methodFlow']['steps']
    loops = [s for s in steps if s['kind'] == 'loop']
    assert [s['controlKind'] for s in loops] == ['for', 'foreach', 'while', 'do']
    assert [s['kind'] for s in loops[0]['update']] == ['call', 'statement', 'call']
    assert [s['label'] for s in loops[0]['condition']] == ['check()']
    terminals = [s['kind'] for s in flatten(loops[0]['body']) if s['kind'] in {'break', 'continue'}]
    assert terminals == ['continue', 'break']
    assert not next(e for e in result['events'] if e['callee'] == 'load')['conditional']
    assert all(e['conditional'] for e in result['events'] if e['callee'] in {'check', 'next', 'again'})
    assert direct('for(;;){continue;}')['methodFlow']['steps'][0]['canExit'] is False


def test_switch_shared_labels_and_expression(tmp_path):
    """Verify switch shared labels and expression."""
    graph, node, _ = production(tmp_path, 'choose')
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert event_names(result) == ['code', 'one', 'other', 'first', 'second']
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'branch', 'branch', 'return']
    assert steps[1]['arms'][0]['label'] == 'case 1 / case 2'
    assert [[s['label'] for s in a['steps']] for a in steps[2]['arms']] == [['first()'], ['second()']]
    assert not result['methodFlow']['unsupported']


def test_deferred_bodies_and_anonymous_class_initializers_stay_separate(tmp_path):
    """Verify deferred bodies and anonymous class initializers stay separate."""
    graph, node, _ = production(tmp_path, 'deferred')
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert event_names(result) == ['argument', 'Base', 'visible']
    assert result['methodFlow']['unsupported']
    assert not any(e['callee'].startswith('hidden') for e in result['events'])


def test_cleanup_is_visible_opaque_without_false_linear_calls(tmp_path):
    """Verify cleanup is visible opaque without false linear calls."""
    graph, node, _ = production(tmp_path, 'cleanup')
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert event_names(result) == ['before', 'after']
    assert [s['kind'] for s in result['methodFlow']['steps']] == ['call', 'unknown', 'call']
    assert result['methodFlow']['steps'][1]['incomplete']
    assert any('try_with_resources' in r for r in result['unresolved'])


@pytest.mark.parametrize('body,reason', [
    ('try{return a();}finally{b();}', 'try_statement'),
    ('switch(x){case 1:a();case 2:b();}', 'fallthrough'),
    ('return switch(x){default -> {yield a();}};', 'yield'),
    ('outer: while(ok()){break outer;}', 'labeled_statement'),
    ('assert ok():message();', 'assert_statement'),
    ('synchronized(lock()){body();}', 'synchronized_statement'),
])
def test_unsupported_contexts_do_not_flatten_their_calls(body, reason):
    """Verify unsupported contexts do not flatten their calls."""
    result = direct(body)
    assert result['events'] == []
    assert any(reason in r for r in result['methodFlow']['unsupported'])
    assert any(s.get('incomplete') for s in flatten(result['methodFlow']['steps']))


def test_ternary_and_boolean_value_arms_and_receiver_order():
    """Verify ternary and boolean value arms and receiver order."""
    result = direct('var v = first() ? 1 : other(); var b = ok || check(); create().send(a(),b());')
    assert event_names(result) == ['first', 'other', 'check', 'create', 'a', 'b', 'send']
    choices = [s for s in result['methodFlow']['steps'] if s['kind'] == 'branch']
    assert choices[0]['arms'][0]['steps'][0]['kind'] == 'value'
    assert choices[1]['arms'][1]['steps'] == []
    assert next(e for e in result['events'] if e['callee'] == 'check')['conditional']
    assert len({(e['line'], e['column'], e['endLine'], e['endColumn']) for e in result['events']}) == 7


def test_missing_ambiguous_stale_and_overloaded_declarations():
    """Verify missing ambiguous stale and overloaded declarations."""
    source = {'text': 'class A {void m(){a();} void m(int x){b();}} class B{void m(){c();}}'}
    result = analyze(source, {'label': '.m()', 'line': 1}, max_events=100)
    assert result['sourceStatus'] == 'unresolved' and result['events'] == []
    source = {'text': 'class A {\nvoid m(){a();}\nvoid m(int x){b();}\n}'}
    node = {'label': '.m()', 'line': 3, 'callableFacts': {'parameterList': '(int x)'}}
    assert event_names(analyze(source, node, max_events=100)) == ['b']
    node['line'] = 100
    assert analyze(source, node, max_events=100)['sourceStatus'] == 'unresolved'
    node['line'] = 2
    assert analyze(source, node, max_events=100)['sourceStatus'] == 'unresolved'
    assert direct('a(')['sourceStatus'] == 'unresolved'


def test_abstract_method_and_no_class_documentation_inheritance():
    """Verify abstract method and no class documentation inheritance."""
    source = {'text': '/** class summary */ abstract class C { abstract void run(); }'}
    result = analyze(source, {'label': '.run()', 'line': 1}, max_events=100)
    assert result['sourceStatus'] == 'parsed' and result['methodFlow']['status'] == 'declaration-only'
    assert result['documentation']['summary'] == '' and result['events'] == []


def test_event_step_depth_ast_and_documentation_budgets():
    """Verify event step depth ast and documentation budgets."""
    limited = direct('a();b();c();', max_events=1)
    assert event_names(limited) == ['a'] and limited['eventsTruncated']
    assert limited['methodFlow']['truncated']
    many = direct('a();' * 300)
    assert len(flatten(many['methodFlow']['steps'])) <= 180 and many['methodFlow']['truncated']
    deep = direct('x(' * 35 + '1' + ')' * 35 + ';')
    assert deep['methodFlow']['truncated'] and any('depth' in r for r in deep['unresolved'])
    huge = direct('int a=1;' * 4000)
    assert huge['sourceStatus'] == 'unavailable' and huge['events'] == []
    source = {'text': 'class C {/**' + 'text ' * 3000 + '*/ void run(){} }'}
    doc = analyze(source, {'label': '.run()', 'line': 1}, max_events=100)['documentation']
    assert doc['truncated'] and len(doc['summary']) <= 8000


def test_missing_grammar_is_explicit(monkeypatch):
    """Verify missing grammar is explicit."""
    import sys
    monkeypatch.setitem(sys.modules, 'tree_sitter_java', None)
    result = direct('work();')
    assert result['sourceStatus'] == 'unavailable'
    assert 'грамматика' in result['unresolved'][0]


def test_utf8_byte_columns_and_no_input_mutation():
    """Verify utf8 byte columns and no input mutation."""
    source = {'text': 'class C {void run(){String s="я"; call("ю");}}'}
    node = {'label': '.run()', 'line': 1}
    before = deepcopy((source, node))
    result = analyze(source, node, max_events=10)
    event = result['events'][0]
    assert event['column'] == source['text'].encode().index(b'call(') + 1
    assert (source, node) == before


def test_parent_steps_survive_budget_and_every_event_has_tree_site():
    """Reserving a control before its children prevents orphan calls after truncation."""
    for body in ('if(ok){' + 'work();' * 180 + '}',
                 'while(ok){' + 'work();' * 180 + '}',
                 'switch(x){case 1 -> {' + 'work();' * 180 + '} default -> stop();}'):
        result = direct(body, max_events=300)
        calls = [s for s in flatten(result['methodFlow']['steps']) if s['kind'] == 'call']
        assert len(flatten(result['methodFlow']['steps'])) <= 180
        assert len(calls) == len(result['events'])
        assert {tuple(s['site'].values()) for s in calls} == {
            tuple(e[k] for k in ('line', 'column', 'endLine', 'endColumn')) for e in result['events']}
        assert result['methodFlow']['steps']


def test_switch_default_token_exhaustiveness_guards_and_arm_budget():
    """A string containing default is a case value, and guards cannot be unconditional."""
    result = direct('switch(x){case "default" -> one();}')
    branch = result['methodFlow']['steps'][0]
    assert branch['arms'][1]['label'] == 'no match'
    value_switch = direct('return switch(x){case 1 -> one();};')
    assert value_switch['events'] == []
    assert any('exhaustiveness' in r for r in value_switch['unresolved'])
    guarded = direct('switch(x){case String s when ok(s) -> one(); default -> other();}')
    assert guarded['events'] == []
    assert any('guards' in r for r in guarded['unresolved'])
    many_arms = direct('switch(x){' + ''.join(f'case {i} -> work();' for i in range(65)) + '}')
    assert many_arms['events'] == [] and any('60 arms' in r for r in many_arms['unresolved'])


def test_compound_conditions_reuse_computed_result_and_plain_statements_survive():
    """Assignments and increments retain source rows after their operand calls."""
    result = direct('if(a() && b()){go();} while(a() || b()){go();} x=read(); i++;')
    steps = result['methodFlow']['steps']
    assert steps[2]['conditionResult'] == 'reuse'
    loop = next(s for s in steps if s['kind'] == 'loop')
    assert loop['conditionResult'] == 'reuse'
    assert [s['label'] for s in steps[-3:]] == ['read()', 'x=read();', 'i++;']


def test_real_java_member_map_can_be_described_without_changing_captured_facts(tmp_path):
    """Real member enrichment supplies raw declaration IDs and source hashes to both APIs."""
    from atlas.member_graph import enrich_member_graph
    (tmp_path / 'FlowCases.java').write_text(FIXTURE.read_text())
    seed = {'nodes': [{'id': 'file:java', 'kind': 'file', 'file': 'FlowCases.java',
                       'label': 'FlowCases.java', 'line': 1}], 'edges': []}
    graph = enrich_member_graph(seed, tmp_path)
    before = deepcopy(graph)
    java_methods = [n for n in graph['nodes'] if n.get('memberFacts', {}).get('kind') in {'method', 'constructor'}]
    assert len(java_methods) >= 6
    for node in java_methods:
        result = build_node_description(tmp_path, graph, node['id'], 'java-map')
        assert result['sourceStatus'] == 'parsed'
        assert result['nodeId'] == node['id']
        assert result['sourceProvenance']['status'] == 'match'
    assert before == graph


def test_owner_context_disambiguates_same_line_types_and_rejects_wrong_owner():
    """An exact declaring type is evidence; a name and line shared by two classes are not."""
    source = {'text': 'class A{void run(){first();}} class B{void run(){second();}}'}
    node = {'label': '.run()', 'line': 1, 'callableFacts': {'declaringType': 'B'}}
    assert event_names(analyze(source, node, max_events=100)) == ['second']
    node['callableFacts']['declaringType'] = 'Missing'
    assert analyze(source, node, max_events=100)['sourceStatus'] == 'unresolved'


def test_large_labels_and_argument_arrays_are_bounded_and_visible():
    """Display clipping never removes exact source sites or silently grows argument payloads."""
    result = direct('consume(' + ','.join('"' + 'x' * 500 + '"' for _ in range(70)) + ');')
    event = result['events'][0]
    assert len(event['sourceExpression']) == 400 and event['sourceExpressionTruncated']
    assert len(event['arguments']) == 64 and event['argumentsTruncated']
    assert all(len(a['expression']) <= 400 and a['expressionTruncated'] for a in event['arguments'])
    assert all(len(s['label']) <= 400 for s in flatten(result['methodFlow']['steps']))


def test_loop_and_nested_branch_arm_references_remain_real_after_truncation():
    """Verify loop and nested branch arm references remain real after truncation."""
    result = direct('if(check()) {while(ok()){if(other()){' + 'work();' * 200 + '}}}', max_events=300)
    arm_ids = {a['id'] for b in result['controlFlow']['branches'] for a in b['arms']}
    assert all(set(e['branchIds']) <= arm_ids for e in result['events'])
    assert all(set(b['parentArmIds']) <= arm_ids for b in result['controlFlow']['branches'])


def test_annotation_arguments_do_not_become_method_body_calls():
    """Annotations describe metadata even if the grammar accepts a nonconstant expression."""
    result = direct('@Anno(notRuntime()) int value=read();')
    assert event_names(result) == ['read']


def test_compact_record_constructor_retains_explicit_body_only():
    """Implicit record field writes, super calls and parameters are not synthesized."""
    result = analyze({'text': 'record C(int x){ C {check(x);} }'},
                     {'label': '.C()', 'line': 1}, max_events=100)
    assert result['sourceStatus'] == 'parsed' and result['kind'] == 'constructor'
    assert event_names(result) == ['check']


def test_java_array_parameter_and_return_dimensions_are_not_lost():
    """Java permits array dimensions after the declared variable or method parameters."""
    result = analyze({'text': 'class C{ int run(C this, int values[])[]{return null;} }'},
                     {'label': '.run()', 'line': 1}, max_events=10)
    assert result['parameters'] == [{'name': 'this', 'type': 'C'}, {'name': 'values', 'type': 'int[]'}]
    assert result['returnType'] == 'int[]'


def test_java_host_keeps_source_safety_and_file_budgets(tmp_path):
    """The adapter receives only safe-reader bytes and cannot bypass the shared host budget."""
    outside = tmp_path.parent / (tmp_path.name + '-outside.java')
    outside.write_text('class C{void run(){secret();}}')
    (tmp_path / 'Link.java').symlink_to(outside)
    node = {'id': 'raw:java', 'label': '.run()', 'file': 'Link.java', 'line': 1}
    graph = {'nodes': [node], 'edges': []}
    try:
        result = build_node_description(tmp_path, graph, node['id'], 's')
        assert result['sourceStatus'] == 'unavailable' and result['events'] == []
        (tmp_path / 'Safe.java').write_text('class C{void run(){safe();}}')
        node['file'] = 'Safe.java'
        analysis = SourceAnalysis(tmp_path, 's', 0, 1024 * 1024, 100)
        assert analysis.describe(node)['sourceStatus'] == 'unavailable'
        assert analysis.truncation['files']
    finally:
        outside.unlink()


def test_switch_statement_arrow_blocks_keep_alternatives_and_terminals():
    """Statement switch blocks need no value-yield join and can use ordinary branch arms."""
    result = direct('switch(code()){case 1 -> {one();return;} default -> {other();}} after();')
    assert event_names(result) == ['code', 'one', 'other', 'after']
    choice = result['methodFlow']['steps'][1]
    assert [[s['kind'] for s in a['steps']] for a in choice['arms']] == [['call', 'return'], ['call']]
    assert not result['methodFlow']['unsupported']
