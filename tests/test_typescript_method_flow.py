"""Real TS/TSX grammar and production descriptions verify source order without executing fixtures."""
from copy import deepcopy
from pathlib import Path

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.typescript import analyze
from atlas.member_graph import enrich_member_graph
from atlas.node_description import build_node_description

FIXTURES = Path(__file__).parent / 'fixtures' / 'flow' / 'typescript'


def raw(source, name='run', *, suffix='.ts', line=1, max_events=160, **fields):
    """Analyze source bytes directly for negative identities and bounded output cases."""
    node = dict(id='raw:run', label=name + '()', line=line, **fields)
    return analyze({'text': source, 'file': 'Code' + suffix}, node, max_events=max_events)


def describe(tmp_path, source, name='run', suffix='.ts'):
    """Exercise real member capture, SourceAnalysis and public node-description integration."""
    filename = 'Code' + suffix
    path = tmp_path / filename
    path.write_text(source)
    initial = {'nodes': [dict(id='file:raw', label=filename, file=filename, kind='file', line=1)], 'edges': []}
    graph = enrich_member_graph(initial, tmp_path)
    node = next(n for n in graph['nodes'] if n.get('memberFacts', {}).get('name') == name and n.get('callableFacts'))
    before = deepcopy(graph)
    analysis = SourceAnalysis(tmp_path, 'ts-snapshot', 5, 1024 * 1024, 160)
    direct = analysis.describe(node)
    result = build_node_description(tmp_path, graph, node['id'], 'ts-snapshot')
    assert result['sourceStatus'] == direct['sourceStatus'] == 'parsed'
    assert result['methodFlow'] == direct['methodFlow']
    assert result['nodeId'] == node['id'] and result['snapshotId'] == 'ts-snapshot'
    assert result['sourceHash'] == direct['sourceHash']
    assert graph == before and path.read_text() == source
    return result


def flattened(steps):
    """Read all lexical alternatives while retaining their placement in the asserted tree."""
    result = []
    for step in steps:
        result.append(step)
        for arm in step.get('arms', []):
            result.extend(flattened(arm['steps']))
        for field in ('condition', 'body', 'update'):
            result.extend(flattened(step.get(field, [])))
    return result


def names(result):
    """Return the unbound lexical call order, including alternatives exactly once."""
    return [e['callee'] for e in result['events']]


def test_typed_method_real_capture_and_own_documentation(tmp_path):
    """Types are metadata, own comments stay distinct, and generic await is ordered correctly."""
    result = describe(tmp_path, (FIXTURES / 'typed.ts').read_text())
    assert result['parameters'] == [{'name': 'item', 'type': 'T'}, {'name': 'option', 'type': 'string'}]
    assert result['returnType'] == 'Promise<T>'
    assert 'T extends Item' in result['signature']
    assert result['documentation']['summary'] == 'Process a typed item.'
    assert result['typeContext']['documentation']['summary'] == 'Class context only.'
    assert names(result) == ['prepare', 'inner', 'wrap', 'ready', 'check', 'persist', 'failure']
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'call', 'call', 'statement', 'call', 'branch', 'branch']
    logical, choice = steps[-2:]
    assert logical['controlKind'] == '&&'
    assert logical['arms'][0]['steps'][0]['label'] == 'check()'
    assert logical['arms'][1]['steps'] == []
    assert [s['kind'] for s in choice['arms'][0]['steps']] == ['call', 'await', 'return']
    assert [s['kind'] for s in choice['arms'][1]['steps']] == ['call', 'throw']
    assert all(e['awaited'] == (e['callee'] == 'persist') and not e['parallel'] for e in result['events'])
    assert result['parameterFacts']['status'] == result['attributeFacts']['status'] == 'unavailable'
    assert not result['methodFlow']['unsupported']


def test_nested_arguments_repeated_chains_and_exact_sites(tmp_path):
    """Receivers and arguments precede outer calls; shared start columns need exclusive ends."""
    result = describe(tmp_path, 'function run(): void { make().build(arg(inner())).send(); ping(); ping(); }')
    assert names(result) == ['make', 'inner', 'arg', 'build', 'send', 'ping', 'ping']
    calls = [s for s in flattened(result['methodFlow']['steps']) if s['kind'] == 'call']
    assert [s['label'] for s in calls] == ['make()', 'inner()', 'arg()', 'make().build()', 'make().build(arg(inner())).send()', 'ping()', 'ping()']
    assert [s['site'] for s in calls] == [{k: e[k] for k in ('line', 'column', 'endLine', 'endColumn')} for e in result['events']]
    assert len({tuple(s['site'].values()) for s in calls}) == 7


@pytest.mark.parametrize('operator,arm', [('&&', 'truthy'), ('||', 'falsy'), ('??', 'nullish')])
def test_short_circuit_arms_keep_right_conditional(operator, arm):
    """No right operand call becomes an unconditional event."""
    result = raw('function run(){ const a=left() ' + operator + ' right(); }')
    assert names(result) == ['left', 'right']
    assert [e['conditional'] for e in result['events']] == [False, True]
    branch = result['methodFlow']['steps'][1]
    assert branch['controlKind'] == operator and branch['arms'][0]['label'] == arm
    assert branch['arms'][1]['emptyReason'] == 'skip'


def test_ternary_literals_else_if_and_nested_guards():
    """Literal outcomes remain visible and nested branch ownership is preserved."""
    result = raw('function run(){ const n=test()?1:2; if(a()){yes();} else if(b()){other();} }')
    steps = result['methodFlow']['steps']
    branch = steps[1]
    assert [[s['expression'] for s in a['steps']] for a in branch['arms']] == [['1'], ['2']]
    outer = steps[-1]
    assert [s['kind'] for s in outer['arms'][1]['steps']] == ['call', 'branch']
    other = next(e for e in result['events'] if e['callee'] == 'other')
    assert len(other['branchIds']) == 2 and other['conditional']


@pytest.mark.parametrize('expression,expected', [
    ('obj?.read(arg())', ['arg', 'read']),
    ('fn?.(arg())', ['arg', 'fn']),
    ('get()?.[key()]', ['get', 'key']),
    ('obj[index()]?.(arg())', ['index', 'arg', '']),
])
def test_optional_access_call_and_computed_index(expression, expected):
    """Receiver calls are evaluated once before a guard; skipped arguments remain conditional."""
    result = raw('function run(){ return ' + expression + '; }')
    assert names(result) == expected
    branch = next(s for s in result['methodFlow']['steps'] if s['kind'] == 'branch')
    assert branch['controlKind'] == '?.' and branch['arms'][1]['emptyReason'] == 'skip'
    outside = {'get', 'index'}
    assert all(e['conditional'] == (e['callee'] not in outside) for e in result['events'])
    assert not result['methodFlow']['unsupported']


def test_lifted_optional_chain_stays_explicitly_opaque():
    """A skipped continuation must never become a linear call after an optional guard."""
    result = raw('function run(){ obj?.one(arg()).two(next()); after(); }')
    assert names(result) == ['after']
    assert result['methodFlow']['steps'][0]['kind'] == 'unknown'
    assert 'optional chain' in result['methodFlow']['unsupported'][0]


def test_loops_separate_one_time_values_conditions_bodies_and_updates(tmp_path):
    """Continue retains its update destination and iterable calls stay outside repeated bodies."""
    result = describe(tmp_path, '''function run(){
      for(init();test();update()){ if(skip())continue; work(); if(done())break; }
      for(const item of items()){ consume(item); }
      while(more()){ next(); }
      do { once(); } while(again());
    }''')
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'loop', 'call', 'loop', 'loop', 'loop']
    loop = steps[1]
    assert [s['label'] for s in loop['condition']] == ['test()']
    assert [s['label'] for s in loop['update']] == ['update()']
    assert [s['kind'] for s in loop['body']] == ['call', 'branch', 'call', 'call', 'branch']
    assert loop['body'][1]['arms'][0]['steps'][0]['kind'] == 'continue'
    assert loop['body'][-1]['arms'][0]['steps'][0]['kind'] == 'break'
    assert steps[2]['label'] == 'items()' and steps[3]['condition'] == []
    assert steps[-1]['controlKind'] == 'do'
    assert names(result)[-2:] == ['once', 'again']


def test_infinite_loop_and_multiple_updates():
    """Empty for conditions cannot claim a natural condition exit."""
    result = raw('function run(){ for(;;){continue;} for(a(),b();check();c(),d()){work();} }')
    first, a, b, second = result['methodFlow']['steps']
    assert first['canExit'] is False
    assert [s['label'] for s in second['update']] == ['c()', 'd()']


def test_type_only_declarations_callbacks_and_default_parameters():
    """Types and deferred bodies are not execution; parameter defaults have explicit guards."""
    result = raw('''function run<T>(x: T = seed()): void {
      type Callback = () => ReturnType<typeof hidden>;
      interface Local { call(): void }
      const f = (x: number): number => hidden(x);
      function nested(){ hiddenToo(); }
      list.map((x: T) => later(x));
      done();
    }''')
    assert names(result) == ['seed', 'map', 'done']
    first = result['methodFlow']['steps'][0]
    assert first['controlKind'] == 'parameter-default' and result['events'][0]['conditional']
    assert len([s for s in flattened(result['methodFlow']['steps']) if s.get('deferred')]) == 3
    assert not result['methodFlow']['unsupported']


def test_tsx_actual_component_expressions_and_callbacks(tmp_path):
    """JSX expression containers evaluate in source order without invented component calls."""
    result = describe(tmp_path, (FIXTURES / 'view.tsx').read_text(), 'View', '.tsx')
    assert result['returnType'] == 'JSX.Element'
    assert names(result) == ['load', 'ready', 'text', 'fallback']
    assert all(e['conditional'] for e in result['events'][-2:])
    assert not result['methodFlow']['unsupported']


@pytest.mark.parametrize('declaration', [
    'declare function run<T>(x: T): Promise<T>;',
    'interface I { run(x: string): void; }',
    'abstract class A { abstract run(x: number): void; }',
])
def test_declaration_signatures_have_no_executable_body(declaration):
    """Ambient/interface/abstract signatures are parsed declaration-only results."""
    result = raw(declaration)
    assert result['sourceStatus'] == 'parsed'
    assert result['methodFlow']['status'] == 'declaration-only' and result['events'] == []


def test_arrow_expression_implicit_return():
    """An explicitly selected typed arrow owns its expression body and implicit return."""
    result = raw('const run = async (x: number): Promise<number> => await compute(x);')
    assert names(result) == ['compute']
    assert [s['kind'] for s in result['methodFlow']['steps']] == ['call', 'await', 'return']
    assert result['controlFlow']['terminals'][0]['kind'] == 'return'


@pytest.mark.parametrize('source,line,fields', [
    ('class A { run(){wrongA();} } class B { run(){wrongB();} }', 1, {}),
    ('function run(){wrong();}', 8, {}),
    ('function other(){wrong();}', 1, {}),
    ('class A { run(){wrongA();} } class B { run(){wrongB();} }', 1, {'callableFacts': {'declaringType': 'C'}}),
    ('function run(x: string): void; function run(x: number): void;', 1, {}),
])
def test_missing_stale_and_ambiguous_identity_fails_closed(source, line, fields):
    """Even a unique neighbour is rejected when the requested source location is stale."""
    result = raw(source, line=line, **fields)
    assert result['sourceStatus'] == 'unresolved' and result['events'] == []
    assert result['methodFlow']['status'] == 'unavailable'


def test_same_name_method_owner_and_nested_function_line():
    """Class ownership and exact nested declaration lines cannot select a sibling method."""
    source = 'class A { run(){wrong();} } class B { run(){right();} }'
    result = raw(source, callableFacts={'declaringType': 'B'})
    assert names(result) == ['right']
    nested = raw('function run(){\n function run(){inner();}\n outer();\n}', line=2)
    assert names(nested) == ['inner']


def test_parse_error_is_not_a_partial_execution_tree():
    """Malformed selected declarations do not leak guessed paths."""
    result = raw('function run(){ if( ){ bad(); } }')
    assert result['sourceStatus'] == 'unresolved'
    assert not result['events'] and 'parse errors' in result['unresolved'][0]


@pytest.mark.parametrize('body,reason', [
    ('try { return work(); } finally { cleanup(); }', 'cleanup'),
    ('switch(code()){case 1: one(); default: other();}', 'control transfer'),
    ('for await (const item of stream()){use(item);}', 'for-await'),
    ('label: while(ok()){break label;}', 'control transfer'),
    ('let {x=hidden()}=value();', 'destructuring'),
    ('x ??= hidden();', 'logical assignment'),
    ('class Inner extends base(){ static x=hidden(); }', 'static initialization'),
    ('for(const {x=hidden()} of values()){work();}', 'destructured iteration'),
])
def test_unsupported_subtrees_are_visible_and_never_flattened(body, reason):
    """Each opaque construct remains visible with no falsely unconditional hidden call."""
    result = raw('async function run(){' + body + 'after();}')
    assert names(result) == ['after']
    assert result['methodFlow']['steps'][0]['incomplete']
    assert any(reason in s for s in result['methodFlow']['unsupported'])


def test_object_computed_names_evaluate_but_method_body_is_deferred():
    """Object method names run at construction time; their bodies retain the closure boundary."""
    result = raw('function run(){ const obj = {[key()]: value(), [method()](){hidden();}}; }')
    assert names(result) == ['key', 'value', 'method']
    assert not result['methodFlow']['unsupported']


def test_generator_has_explicit_resume_limitation():
    """A generator cannot claim its body executes as part of an ordinary function call."""
    result = raw('function* run(){hidden(); yield produce();}')
    assert not result['events']
    assert 'generator body' in result['methodFlow']['unsupported'][0]


def test_limits_events_steps_depth_ast_and_long_labels():
    """Independent budgets are visible and cannot leak unbounded labels or false complete trees."""
    result = raw('function run(){a();b();c();}', max_events=1)
    assert names(result) == ['a'] and result['eventsTruncated']
    assert any('max_events' in s for s in result['unresolved'])
    large = raw('function run(){' + 'call();' * 250 + '}')
    assert large['methodFlow']['truncated']
    assert len(flattened(large['methodFlow']['steps'])) <= 180
    deep = raw('function run(){' + 'if(ok){' * 40 + 'hidden();' + '}' * 40 + '}')
    assert deep['methodFlow']['truncated'] and 'hidden' not in names(deep)
    enormous = raw('function run(){' + 'call();' * 6000 + '}')
    assert enormous['methodFlow']['status'] == 'unavailable' and enormous['methodFlow']['truncated']
    long = raw('function run(){' + 'x' * 700 + '();}')
    assert all(len(s['label']) <= 400 for s in flattened(long['methodFlow']['steps']))
    assert all(len(e['text']) <= 400 and len(e['sourceExpression']) <= 400 for e in long['events'])


def test_utf8_sites_use_real_parser_byte_offsets():
    """UTF-8 prefixes do not mix Python character counts with Tree-sitter byte columns."""
    source = 'function run(){ const name="Привет"; target(arg()); }'
    result = raw(source)
    expected = source.encode().index(b'target(') + 1
    target = next(e for e in result['events'] if e['callee'] == 'target')
    assert target['column'] == expected
    assert target['endColumn'] - target['column'] == len('target(arg())')


def test_public_description_binds_only_existing_raw_ids_and_preserves_source(tmp_path):
    """The adapter never invents raw identities; the shared binder uses graph evidence only."""
    source = 'function run(){\n target(arg());\n}\nfunction target(x: unknown){}\nfunction arg(){}'
    path = tmp_path / 'Code.ts'
    path.write_text(source)
    graph = enrich_member_graph({'nodes': [dict(id='file:raw', label='Code.ts', file='Code.ts', kind='file', line=1)], 'edges': []}, tmp_path)
    nodes = {n.get('memberFacts', {}).get('name'): n for n in graph['nodes'] if n.get('callableFacts')}
    graph['edges'].extend(dict(source=nodes['run']['id'], target=nodes[name]['id'], relation='calls', sourceLine=2, confidence='source') for name in ('target', 'arg'))
    before = deepcopy(graph)
    result = build_node_description(tmp_path, graph, nodes['run']['id'], 'ts-snapshot')
    assert [e['targetNodeIds'] for e in result['events']] == [[nodes['arg']['id']], [nodes['target']['id']]]
    assert graph == before and path.read_text() == source


def test_missing_grammar_reports_unavailable(monkeypatch):
    """Unavailable local dependencies are reported without install attempts or C# fallback."""
    import sys
    monkeypatch.setitem(sys.modules, 'tree_sitter_typescript', None)
    result = raw('function run(){hidden();}')
    assert result['sourceStatus'] == 'unavailable' and not result['events']


def test_control_flow_arm_references_are_resolvable():
    """Events and terminals reference actual controlFlow arms, including nested loop ownership."""
    result = raw('function run(x=seed()){ if(check() && ready()){ for(const item of items()){ if(skip())continue; work(); } } return done(); }')
    branches = result['controlFlow']['branches']
    arm_ids = {a['id'] for b in branches for a in b['arms']}
    for event in result['events'] + result['controlFlow']['terminals']:
        assert set(event['branchIds']) <= arm_ids
    assert all(set(b['parentArmIds']) <= arm_ids for b in branches)
    choice = next(s for s in result['methodFlow']['steps'] if s.get('controlKind') == 'if')
    assert choice['conditionResult'] == 'reuse'


def test_class_node_never_borrows_same_named_method():
    """Routing a non-callable map node cannot disclose its same-name member as its own body."""
    result = raw('class run { run(){wrong();} }', kind='class')
    assert result['sourceStatus'] == 'unresolved' and result['events'] == []


def test_await_optional_generic_and_typed_wrappers():
    """Typed erasure wrappers and optional calls retain the enclosing await boundary."""
    result = raw('async function run(){ const x = read() as Foo; const y = get() satisfies Foo; const z = <Foo>other(); return await obj?.read(arg()); }')
    assert names(result) == ['read', 'get', 'other', 'arg', 'read']
    assert result['events'][-1]['conditional'] and result['events'][-1]['awaited']
    assert not result['events'][-2]['awaited']
    assert [s['kind'] for s in result['methodFlow']['steps'][-3:]] == ['branch', 'await', 'return']
    assert not result['methodFlow']['unsupported']


def test_full_pipeline_unresolved_and_declaration_only(tmp_path):
    """Negative adapter results also satisfy the public description schema."""
    path = tmp_path / 'Code.ts'
    path.write_text('declare function run(x: string): void;')
    node = dict(id='raw:signature', label='run()', file='Code.ts', kind='method', line=1)
    graph = {'nodes': [node], 'edges': []}
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert result['methodFlow']['status'] == 'declaration-only'
    path.write_text('function different(){wrong();}')
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert result['sourceStatus'] == 'unresolved' and not result['events']


@pytest.mark.parametrize('expression,expected', [
    ('await obj?.read<T>(arg())', ['arg', 'read']),
    ('await obj?.[key()]<T>(arg())', ['key', 'arg', '']),
    ('await fn?.<T>(arg())', ['arg', 'fn']),
])
def test_generic_optional_await_never_lifts_arguments_outside_guard(expression, expected):
    """Installed grammar await-callee shapes cannot turn optional arguments unconditional."""
    result = raw('async function run(){ return ' + expression + '; }')
    assert names(result) == expected
    assert all(e['conditional'] for e in result['events'])
    assert result['events'][-1]['awaited']
    branch, suspension, terminal = result['methodFlow']['steps']
    assert [s['kind'] for s in branch['arms'][0]['steps']] == ['call'] * len(expected)
    assert suspension['kind'] == 'await' and terminal['kind'] == 'return'
    assert not result['methodFlow']['unsupported']


def test_transparent_await_wrappers_and_loop_condition_ownership():
    """Parentheses do not remove direct-await facts; first loop tests precede body membership."""
    result = raw('async function run(){ await (get<T>()); while(check()){work();} }')
    assert result['events'][0]['awaited']
    check = next(e for e in result['events'] if e['callee'] == 'check')
    work = next(e for e in result['events'] if e['callee'] == 'work')
    assert not check['conditional'] and check['branchIds'] == []
    assert work['conditional'] and work['branchIds']


def test_rest_destructuring_and_assignment_are_explicit_not_silently_erased():
    """Nested rest patterns execute defaults, unlike TypeScript's erased this parameter."""
    simple = raw('function run(this: Context, ...args: number[]){done();}')
    assert names(simple) == ['done'] and not simple['methodFlow']['unsupported']
    rest = raw('function run(...[x=hidden()]){done();}')
    assert names(rest) == ['done']
    assert 'destructured parameter' in rest['methodFlow']['unsupported'][0]
    assignment = raw('function run(){ ({x=hidden()}=value()); after(); }')
    assert names(assignment) == ['after']
    assert 'destructuring assignments' in assignment['methodFlow']['unsupported'][0]


def test_delete_side_effect_keeps_source_operation():
    """A computed receiver call does not replace the surrounding delete operation."""
    result = raw('function run(){ delete target()[key()]; }')
    assert names(result) == ['target', 'key']
    assert result['methodFlow']['steps'][-1]['label'] == 'delete target()[key()]'
    assert result['methodFlow']['steps'][-1]['kind'] == 'statement'


def test_arrow_field_capture_reaches_common_provider_after_host_integration(tmp_path):
    """The coordinator closed field routing; TSX prop expressions retain their actual lexical calls."""
    path = tmp_path / 'Arrow.tsx'
    path.write_text('const View = (props: Props): JSX.Element => <Panel value={load()}/>;')
    graph = enrich_member_graph({'nodes': [dict(id='file', label='Arrow.tsx', file='Arrow.tsx', line=1)], 'edges': []}, tmp_path)
    node = next(n for n in graph['nodes'] if n.get('memberFacts', {}).get('name') == 'View')
    assert node.get('fieldFacts') and node['callableFacts']['functionValued']
    before = deepcopy(node)
    result = SourceAnalysis(tmp_path, 's', 5, 1024 * 1024, 160).describe(node)
    assert result['kind'] == 'method' and result['methodFlow']['status'] == 'parsed'
    assert result['flowProvider']['id'] == 'typescript'
    assert [event['callee'] for event in result['events']] == ['load']
    assert result['methodFlow']['steps'][-1]['kind'] == 'return'
    assert node == before


def test_call_events_and_source_inputs_are_independent_and_zero_budget_is_honest():
    """Adapter reads neither source dictionaries nor raw graph node facts as mutable state."""
    source = {'text': 'function run(){ call(); }', 'file': 'Code.ts', 'sourceByteHash': 'known'}
    node = {'id': 'raw:id', 'label': 'run()', 'line': 1}
    before = deepcopy((source, node))
    result = analyze(source, node, max_events=0)
    assert (source, node) == before
    assert result['events'] == [] and result['eventsTruncated']
    assert result['methodFlow']['steps'][0]['kind'] == 'call'


@pytest.mark.parametrize('expression', [
    'await get<T>().then(arg())',
    'await get<T>()[key()](arg())',
    'await get<T>().value',
])
def test_generic_await_receiver_continuation_cannot_suspend_too_early(expression):
    """Ambiguous grammar precedence must not move await ahead of a continuing member/call."""
    result = raw('async function run(){ return ' + expression + '; }')
    assert not result['events']
    assert result['methodFlow']['steps'][0]['kind'] == 'unknown'
    assert 'generic await receiver continuation' in result['methodFlow']['unsupported'][0]


def test_parenthesized_generic_await_chain_has_an_explicit_safe_boundary():
    """Parentheses distinguish awaiting the whole chain from continuing an awaited result."""
    whole = raw('async function run(){ return await (get<T>().then(arg())); }')
    assert names(whole) == ['get', 'arg', 'then']
    assert [e['awaited'] for e in whole['events']] == [False, False, True]
    assert [s['kind'] for s in whole['methodFlow']['steps']] == ['call', 'call', 'call', 'await', 'return']
    receiver = raw('async function run(){ return (await get<T>()).then(arg()); }')
    assert names(receiver) == ['get', 'arg', 'then']
    assert [s['kind'] for s in receiver['methodFlow']['steps']] == ['call', 'await', 'call', 'call', 'return']
    assert not whole['methodFlow']['unsupported'] and not receiver['methodFlow']['unsupported']
