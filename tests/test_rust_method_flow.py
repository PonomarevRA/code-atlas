"""Source-only Rust flow acceptance through production host and description binding."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import builtins

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.rust import analyze
from atlas.member_graph import enrich_member_graph
from atlas.node_description import build_node_description

FIXTURES = Path(__file__).parent / 'fixtures' / 'flow' / 'rust'


def description(tmp_path, source, *, name='run', line=1, column=None, max_events=160):
    """Use the real safe source host with a preserved raw method identity."""
    path = tmp_path / 'lib.rs'
    path.write_text(source)
    node = {'id': 'raw:rust::' + name, 'label': name + '()', 'kind': 'method',
            'file': 'lib.rs', 'line': line, 'memberFacts': {'language': 'rust', 'name': name}}
    if column is not None:
        node['column'] = column
    analysis = SourceAnalysis(tmp_path, 'rust-fixture', 5, 1024 * 1024, max_events)
    return analysis.describe(node)


def steps(result):
    """Return visible root steps without flattening alternative paths."""
    return result['methodFlow']['steps']


def all_steps(items):
    """Traverse every lexical arm/loop for invariant checks, not as an execution path."""
    result = []
    for item in items:
        result.append(item)
        for arm in item.get('arms', []):
            result.extend(all_steps(arm['steps']))
        for key in ('condition', 'body', 'update'):
            result.extend(all_steps(item.get(key, [])))
    return result


def test_nested_arguments_receiver_and_await_order(tmp_path):
    """Receiver and nested arguments precede the outer call; only its result is awaited."""
    result = description(tmp_path, 'async fn run(){ return make().send(inner(), other(nested())).await; }')
    assert [e['callee'] for e in result['events']] == ['make', 'inner', 'nested', 'other', 'send']
    assert [s['kind'] for s in steps(result)] == ['call'] * 5 + ['await', 'return']
    assert [e['awaited'] for e in result['events']] == [False] * 4 + [True]
    assert steps(result)[4]['directAwait']
    assert all(not e['parallel'] for e in result['events'])
    assert not result['eventsTruncated']


def test_branches_short_circuit_and_value_arms(tmp_path):
    """Short circuit RHS and selected results remain conditional, including literal arms."""
    result = description(tmp_path, 'fn run(){ if left() && right() { yes(); } else { no(); } let x=if a {10} else {20}; }')
    assert [e['callee'] for e in result['events']] == ['left', 'right', 'yes', 'no']
    assert [e['conditional'] for e in result['events']] == [False, True, True, True]
    choices = [s for s in steps(result) if s['kind'] == 'branch']
    assert choices[0]['controlKind'] == '&&'
    assert choices[0]['arms'][1]['emptyReason'] == 'skip'
    assert [[s['label'] for s in a['steps']] for a in choices[1]['arms']] == [['yes()'], ['no()']]
    assert [[s['expression'] for s in a['steps']] for a in choices[2]['arms']] == [['10'], ['20']]
    assert result['events'][2]['branchIds'] != result['events'][3]['branchIds']


def test_if_let_let_else_and_question_residual(tmp_path):
    """? and let-else preserve returning failure paths; subsequent calls are conditional."""
    result = description(tmp_path, 'fn run(){ let Some(x)=read()? else {return fallback();}; if let Some(y)=find(x){use_it(y);} after(); }')
    assert [e['callee'] for e in result['events']] == ['read', 'fallback', 'find', 'use_it', 'after']
    choices = [s for s in steps(result) if s['kind'] == 'branch']
    assert [s['controlKind'] for s in choices] == ['?', 'let-else', 'if']
    assert choices[0]['arms'][1]['steps'][0]['kind'] == 'return'
    assert choices[1]['arms'][1]['steps'][-1]['kind'] == 'return'
    assert all(e['conditional'] for e in result['events'][1:])
    assert choices[2]['arms'][1]['emptyReason'] == 'skip'


def test_match_order_and_guard_is_opaque(tmp_path):
    """A guard may resume later arms; never render its result body as a linear arm."""
    plain = description(tmp_path, 'fn run()->i32 {match select(){1|2=>wrap(inner()),_=>0}}')
    assert [e['callee'] for e in plain['events']] == ['select', 'inner', 'wrap']
    match = steps(plain)[1]
    assert match['controlKind'] == 'match'
    assert [a['label'] for a in match['arms']] == ['1|2', '_']
    assert match['arms'][1]['steps'][0]['expression'] == '0'
    assert steps(plain)[-1]['implicit']
    guarded = description(tmp_path, 'fn run(){match select(){Some(x) if guard(x)=>work(x),_=>other()};after();}')
    assert [e['callee'] for e in guarded['events']] == ['after']
    assert steps(guarded)[0]['incomplete']
    assert any('match guards' in reason for reason in guarded['unresolved'])


def test_loops_iterable_once_conditions_repeat_and_jumps(tmp_path):
    """for loads once; while checks inside its loop; jumps terminate only their local path."""
    result = description(tmp_path, 'fn run(){for v in load(){if skip(v){continue;} work(v); if done(){break;} } while check(){step();} after();}')
    root = steps(result)
    assert [s['kind'] for s in root] == ['call', 'loop', 'loop', 'call']
    assert root[1]['controlKind'] == 'foreach' and root[1]['condition'] == []
    assert [s['label'] for s in root[2]['condition']] == ['check()']
    assert root[1]['body'][1]['arms'][0]['steps'][-1]['kind'] == 'continue'
    assert root[1]['body'][-1]['arms'][0]['steps'][-1]['kind'] == 'break'
    assert [e['callee'] for e in result['events']].count('load') == 1
    assert result['events'][0]['conditional'] is False
    assert all(e['conditional'] for e in result['events'][1:])


def test_infinite_loop_ignores_nested_break(tmp_path):
    """A nested loop exit cannot make the outer loop fall through to after()."""
    result = description(tmp_path, 'fn run(){loop {loop {break;} work();} after();}')
    assert steps(result)[0]['canExit'] is False
    assert [e['callee'] for e in result['events']] == ['work']
    exits = description(tmp_path, 'fn run(){loop {if done(){break;} work();} after();}')
    assert steps(exits)[0]['canExit'] is False
    assert steps(exits)[0]['hasBreakExit'] is True
    assert exits['events'][-1]['callee'] == 'after'


def test_return_and_break_do_not_create_fallthrough(tmp_path):
    """Source after an unconditional jump is omitted, never shown as the next call."""
    returned = description(tmp_path, 'fn run(){return first(); unreachable();}')
    assert [e['callee'] for e in returned['events']] == ['first']
    assert steps(returned)[-1]['kind'] == 'return'
    broken = description(tmp_path, 'fn run(){loop {break value(); unreachable();} after();}')
    assert [e['callee'] for e in broken['events']] == ['value', 'after']


def test_deferred_macros_and_special_blocks_are_visible_boundaries(tmp_path):
    """Source tokens inside deferred/macros/unsafe are not promoted to outer invocations."""
    source = 'fn run(){let f=|x|hidden(x);let a=async {hidden2().await};fn local(){hidden3();} spawn(async {hidden4()}); opaque!(hidden5());unsafe{hidden6();} after();}'
    result = description(tmp_path, source)
    assert [e['callee'] for e in result['events']] == ['spawn', 'after']
    assert len([s for s in all_steps(steps(result)) if s.get('incomplete')]) == 6
    assert any('macro expansion' in u for u in result['unresolved'])
    assert any('async_block' in u for u in result['unresolved'])


def test_trait_declaration_and_own_rustdoc(tmp_path):
    """Trait signatures remain declaration-only and class/module docs are not inherited."""
    source = '//! Module notes\n/// Trait notes\ntrait T {\n/// Own contract\nfn run(&self,x:i32)->i32;\nfn plain(&self);\n}'
    result = description(tmp_path, source, line=5)
    assert result['methodFlow']['status'] == 'declaration-only'
    assert result['documentation']['summary'] == 'Own contract'
    assert result['parameters'] == [{'name':'self','type':'&self'},{'name':'x','type':'i32'}]
    assert result['returnType'] == 'i32'
    plain = description(tmp_path, source, name='plain', line=6)
    assert plain['documentation']['summary'] == ''
    assert plain['attributeFacts']['status'] == 'unavailable'
    assert plain['parameterFacts']['status'] == 'unavailable'


def test_exact_impl_identity_and_negative_ambiguity(tmp_path):
    """Duplicate names require a real exact range; absent/stale names do not select neighbors."""
    source = 'impl A {\n fn run(){a();}\n}\nimpl B {\n fn run(){b();}\n}'
    assert [e['callee'] for e in description(tmp_path, source, line=2)['events']] == ['a']
    assert [e['callee'] for e in description(tmp_path, source, line=5)['events']] == ['b']
    assert description(tmp_path, source, line=4)['sourceStatus'] == 'unresolved'
    assert description(tmp_path, source, name='absent', line=2)['sourceStatus'] == 'unresolved'
    direct = analyze({'text':source}, {'label':'run()'}, max_events=10)
    assert direct['sourceStatus'] == 'unresolved'
    same_line = 'impl A {fn run(){a();}} impl B {fn run(){b();}}'
    ambiguous = description(tmp_path, same_line)
    assert ambiguous['sourceStatus'] == 'unresolved' and not steps(ambiguous)


def test_local_same_name_range_does_not_select_outer_body(tmp_path):
    """Local declarations are separate; the outer method never acquires their calls."""
    source = 'fn run(){\n fn run(){local();}\n outer();\n}'
    outer = description(tmp_path, source, line=1)
    local = description(tmp_path, source, line=2)
    assert [e['callee'] for e in outer['events']] == ['outer']
    assert [e['callee'] for e in local['events']] == ['local']


def test_parse_error_and_missing_grammar_are_explicit(tmp_path, monkeypatch):
    """Recovery AST and unavailable dependency cannot yield a claimed parsed tree."""
    result = description(tmp_path, 'fn run(){let x=;}')
    assert result['sourceStatus'] == 'unavailable' and steps(result) == []
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        """Simulate an absent installed grammar without changing environment dependencies."""
        if name == 'tree_sitter_rust':
            raise ImportError('fixture missing grammar')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    missing = analyze({'text':'fn run(){work();}'}, {'label':'run','line':1}, max_events=10)
    assert missing['sourceStatus'] == 'unavailable' and not steps(missing)
    assert 'grammar unavailable' in missing['unresolved'][0]


def test_step_depth_ast_and_event_budgets(tmp_path):
    """All limits are observable without recursion overflow or partial fake declarations."""
    many = description(tmp_path, 'fn run(){' + 'work();' * 250 + '}', max_events=2)
    assert many['eventsTruncated'] and len(many['events']) == 2
    assert many['methodFlow']['truncated'] and len(all_steps(steps(many))) <= 180
    deep = description(tmp_path, 'fn run(){' + 'if a {' * 35 + 'work();' + '}' * 35 + '}')
    assert deep['methodFlow']['truncated']
    assert any('depth exceeded' in u for u in deep['unresolved'])
    large = description(tmp_path, 'fn run(){' + 'work();' * 7000 + '}')
    assert large['sourceStatus'] == 'unavailable'
    assert large['methodFlow']['truncated'] and 'AST node budget' in large['unresolved'][0]


def test_loop_owner_survives_step_exhaustion(tmp_path):
    """Budget exhaustion keeps the loop boundary instead of exposing body calls at root."""
    result = description(tmp_path, 'fn run(){while check(){' + 'work();' * 250 + '}}')
    assert result['methodFlow']['truncated']
    assert len(steps(result)) == 1 and steps(result)[0]['kind'] == 'loop'
    assert len(all_steps(steps(result))) <= 180


def test_assignment_order_turbofish_and_const_array_boundary(tmp_path):
    """Rust assignment RHS precedes place; const array length is not a runtime call."""
    result = description(tmp_path, 'fn run(){ values()[index()]=read(); obj.send::<T>(arg()); let a=[repeat(); count()]; x+=compound();}')
    assert [e['callee'] for e in result['events']] == ['read', 'values', 'index', 'arg', 'send']
    assert any('array length/const' in u for u in result['unresolved'])
    assert any('compound assignment' in u for u in result['unresolved'])


def test_production_fixture_source_hash_graph_raw_ids_and_binding(tmp_path):
    """Production describe binds only existing graph targets and preserves all input artifacts."""
    source = (FIXTURES / 'controls.rs').read_text()
    path = tmp_path / 'lib.rs'
    path.write_text(source)
    vendor = {'nodes':[{'id':'raw:run','label':'run()','kind':'method','file':'lib.rs','line':10},
                       {'id':'raw:prepare','label':'prepare()','kind':'method','file':'lib.rs','line':36}],
              'edges':[{'source':'raw:run','target':'raw:prepare','relation':'calls','source_location':'L11','confidence':'EXTRACTED'}]}
    before = deepcopy(vendor)
    digest = sha256(path.read_bytes()).hexdigest()
    # This is the real existing language member pipeline, not a replacement helper.
    graph = enrich_member_graph(vendor, tmp_path)
    assert vendor == before
    graph_before = deepcopy(graph)
    node = next(n for n in graph['nodes'] if n['id'] == 'raw:run')
    direct = SourceAnalysis(tmp_path,'rust-fixture',5,1024*1024,160).describe(node)
    result = build_node_description(tmp_path, graph, node['id'], 'rust-fixture')
    assert result['sourceStatus'] == direct['sourceStatus'] == 'parsed'
    assert result['methodFlow'] == direct['methodFlow']
    assert result['sourceByteHash'] == result['sourceHash'] == digest
    assert result['nodeId'] == 'raw:run'
    assert result['events'][0]['targetNodeIds'] == ['raw:prepare']
    assert graph == graph_before and path.read_text() == source
    assert [e['callee'] for e in result['events']] == ['prepare','load','fallback','find','accept','reject','classify','inner','first','second','items','skip','process','done','next','consume','argument','finish']
    assert 'Loads input' in result['documentation']['summary']
    assert 'Module comments' not in result['documentation']['summary']
    ids = {arm['id'] for b in result['controlFlow']['branches'] for arm in b['arms']}
    assert all(set(e['branchIds']) <= ids for e in result['events'])
    calls = [s for s in all_steps(steps(result)) if s['kind']=='call']
    assert [s['site'] for s in calls] == [{k:e[k] for k in ('line','column','endLine','endColumn')} for e in result['events']]


def test_utf8_byte_columns_and_label_limits(tmp_path):
    """Cyrillic prefix changes byte columns, never call site alignment or label caps."""
    source = 'fn run(){ let текст="привет"; work("' + 'я' * 500 + '"); }'
    result = description(tmp_path, source)
    event = result['events'][0]
    assert event['column'] == len(source[:source.index('work')].encode()) + 1
    assert event['endColumn'] == len(source[:source.rindex(')')+1].encode()) + 1
    assert len(event['sourceExpression']) <= 400
    assert all(len(s['label']) <= 400 for s in all_steps(steps(result)))


def test_empty_real_arms_and_while_let(tmp_path):
    """Empty blocks are skip, while-let initializer effects belong to the loop test."""
    result = description(tmp_path, 'fn run(){if ready {} else {} while let Some(x)=next(){use_it(x);}}')
    assert all(a['emptyReason']=='skip' and a['steps']==[] for a in steps(result)[0]['arms'])
    assert [s['label'] for s in steps(result)[1]['condition']] == ['next()']


@pytest.mark.parametrize('body', [
    'let x={return early();};bad();',
    'let x=outer(return early(),bad()); after();',
    'let x=if a{return early();}else{return other();};bad();',
    'while {return early();}{bad();} after();',
    'for x in {return early();}{bad();} after();',
    'place()[bad()]={return early();};after();',
    '(return early()).await;bad();',
    '(return early())?;bad();',
    '(return early()) && bad();',
])
def test_diverging_subexpressions_do_not_resume_surrounding_source(tmp_path, body):
    """Returning inside initializers/operands/conditions cannot become a later statement."""
    result = description(tmp_path, 'async fn run(){' + body + '}')
    assert result['sourceStatus'] == 'parsed'
    assert all(e['callee'] in {'early','other'} for e in result['events'])
    assert all(e['callee'] not in {'bad','after','outer','place'} for e in result['events'])


def test_statement_and_match_attributes_remain_opaque(tmp_path):
    """cfg-conditioned statements and arms cannot become unconditional source calls."""
    attributed = description(tmp_path, 'fn run(){before();#[cfg(feature="x")] conditional();after();}')
    assert [e['callee'] for e in attributed['events']] == ['before']
    assert any('attributes/cfg' in u for u in attributed['unresolved'])
    matching = description(tmp_path, 'fn run(){match value {#[cfg(feature="x")] 1=>hidden(), _=>other()};}')
    assert matching['events'] == []
    assert any('attributes/cfg' in u for u in matching['unresolved'])


def test_unreachable_break_does_not_give_infinite_loop_an_exit(tmp_path):
    """A break after a return is not a path out of the surrounding loop."""
    result = description(tmp_path, 'fn run(){loop{return early();break;} after();}')
    assert [e['callee'] for e in result['events']] == ['early']
    assert steps(result)[0]['hasBreakExit'] is False


def test_short_circuit_decision_uses_only_computed_left_operand(tmp_path):
    """The RHS is evaluated in its arm, and the consuming if reuses the computed bool."""
    result = description(tmp_path, 'fn run(){if left() || right(){yes();}}')
    branch, consuming = [s for s in steps(result) if s['kind']=='branch']
    assert branch['label'] == 'left()'
    assert consuming['conditionResult'] == 'reuse'
    assert consuming['conditionExpression'] == 'left() || right()'


def test_value_only_property_and_arithmetic_arms_are_not_empty(tmp_path):
    """Property/operand values must survive blocks even when they contain no explicit call."""
    result = description(tmp_path, 'fn run(){let x=if a{x+1}else{obj.field};let y=match tag{1=>a,_=>b};}')
    choices = [s for s in steps(result) if s['kind']=='branch']
    assert [[s['expression'] for s in arm['steps']] for arm in choices[0]['arms']] == [['x+1'],['obj.field']]
    assert [[s['expression'] for s in arm['steps']] for arm in choices[1]['arms']] == [['a'],['b']]


def test_direct_adapter_does_not_mutate_supplied_source_or_node():
    """Adapter facts never modify raw IDs, captured metadata, or host-provided source."""
    source = {'text':'impl A {fn run(){work();}}','file':'lib.rs','sourceByteHash':'captured'}
    node = {'id':'raw:untouched','label':'run','line':1,'memberFacts':{'name':'run','language':'rust'}}
    before = deepcopy((source,node))
    result = analyze(source,node,max_events=20)
    assert result['sourceStatus']=='parsed' and (source,node)==before
    assert all(e['targetNodeIds']==[] for e in result['events'])


def test_parenthesized_await_keeps_only_producing_call_awaited(tmp_path):
    """Transparent parentheses do not detach await metadata from the result-producing call."""
    result = description(tmp_path, 'async fn run(){return ((work(arg()))).await;}')
    assert [e['callee'] for e in result['events']]==['arg','work']
    assert [e['awaited'] for e in result['events']]==[False,True]
    assert steps(result)[1]['directAwait']


def test_same_line_methods_use_explicit_owner_or_column():
    """A real captured owner or exact start column can disambiguate same-line impl methods."""
    source = 'impl A {fn run(){a();}} impl B {fn run(){b();}}'
    node = {'label':'run','line':1,'callableFacts':{'declaringType':'implB'}}
    selected = analyze({'text':source},node,max_events=20)
    assert [e['callee'] for e in selected['events']]==['b']
    exact = analyze({'text':source},{'label':'run','line':1,'column':source.index('fn run')+1},max_events=20)
    assert [e['callee'] for e in exact['events']]==['a']
    stale = analyze({'text':source},{'label':'run','line':1,'column':2},max_events=20)
    assert stale['sourceStatus']=='unresolved' and stale['events']==[]


def test_default_trait_method_has_body_and_own_documentation(tmp_path):
    """Trait default implementations are analyzed independently of bodyless trait signatures."""
    result = description(tmp_path, '/// Trait notes\ntrait T{\n/// Default method notes\nfn run(&self)->i32{read()}\n}',line=4)
    assert result['sourceStatus']=='parsed' and result['methodFlow']['status']=='parsed'
    assert [e['callee'] for e in result['events']]==['read']
    assert result['documentation']['summary']=='Default method notes'
    assert steps(result)[-1]['implicitReturn']
