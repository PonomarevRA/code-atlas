"""Kotlin contract tests use real host entry points and never execute fixture code."""
from copy import deepcopy
from pathlib import Path
import builtins

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.node_description import build_node_description
from atlas.flow_languages.kotlin import analyze

FIXTURE = Path(__file__).parent / 'fixtures/flow/kotlin/Reader.kt'


def describe(tmp_path, source, name='run', line=1, **facts):
    """Describe the exact raw symbol through both production source/description APIs."""
    path = tmp_path / 'Code.kt'
    path.write_text(source, encoding='utf-8')
    node = {'id': 'raw:' + name, 'label': name + '()', 'file': 'Code.kt', 'line': line, **facts}
    graph = {'nodes': [node], 'edges': [], 'metadata': {'snapshotId': 's'}}
    original = deepcopy(graph)
    analysis = SourceAnalysis(tmp_path, 's', 4, 1024 * 1024, 160)
    direct = analysis.describe(node)
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert result['methodFlow'] == direct['methodFlow']
    assert graph == original and path.read_text(encoding='utf-8') == source
    assert result['sourceHash'] == direct['sourceHash']
    assert result['nodeId'] == node['id']
    return result


def flatten(steps):
    """Inspect ownership recursively without pretending the arms form a runtime trace."""
    result = []
    for step in steps:
        result.append(step)
        for arm in step.get('arms', []):
            result.extend(flatten(arm['steps']))
        fields = ('body', 'condition', 'update') if step.get('controlKind') == 'do' else ('condition', 'body', 'update')
        for field in fields:
            result.extend(flatten(step.get(field, [])))
    return result


def calls(result):
    """Use extraction order, preserving duplicate source occurrences."""
    return [e['callee'] for e in result['events']]


def test_fixture_evaluation_and_deferred_scopes(tmp_path):
    """The full fixture preserves evaluation, ownership, suspension boundaries and source identity."""
    source = FIXTURE.read_text()
    result = describe(tmp_path, source, 'read', 4, callableFacts={'declaringType': 'Reader'})
    assert result['sourceStatus'] == 'parsed'
    assert result['analyzerVersion'].startswith('kotlin-')
    assert result['parameters'] == [{'name': 'value', 'type': 'String?'}, {'name': 'ready', 'type': 'Boolean'}]
    assert result['returnType'] == 'Int'
    assert result['documentation']['summary'] == '/** Read a value using lexical alternatives. */'
    assert calls(result) == ['inner', 'outer', 'check', 'yes', 'no', 'argument', 'trim', 'fallback',
                             'items', 'skip', 'work', 'done', 'again', 'more', 'tick', 'once', 'repeat',
                             'context', 'launch', 'let', 'async', 'await', 'finish']
    assert all(not e['parallel'] and not e['awaited'] for e in result['events'])
    assert result['methodFlow']['unsupported']
    assert result['parameterFacts']['status'] == result['attributeFacts']['status'] == 'unavailable'
    assert 'C#' not in result['parameterFacts']['warning']
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps[:5]] == ['call', 'call', 'statement', 'call', 'branch']
    assert [[s['label'] for s in a['steps']] for a in steps[4]['arms']] == [['yes()'], ['no()']]
    assert not result['methodFlow']['truncated']
    all_steps = flatten(steps)
    sites = [s['site'] for s in all_steps if s['kind'] == 'call']
    assert len(sites) == len(result['events'])
    assert sites == [{k: e[k] for k in ('line', 'column', 'endLine', 'endColumn')} for e in result['events']]
    arm_ids = {arm['id'] for b in result['controlFlow']['branches'] for arm in b['arms']}
    assert all(set(e['branchIds']) <= arm_ids for e in result['events'])
    assert [s['kind'] for s in all_steps if s['kind'] in {'continue', 'break', 'return'}] == ['continue', 'break', 'return']


def test_expression_body_return_and_throw(tmp_path):
    """Expression bodies terminate with their actual return or exceptional exit."""
    source = FIXTURE.read_text()
    expression = describe(tmp_path, source, 'expression', 42)
    assert calls(expression) == ['inner', 'wrap']
    assert [s['kind'] for s in expression['methodFlow']['steps']] == ['call', 'call', 'return']
    failure = describe(tmp_path, source, 'fail', 44)
    assert calls(failure) == ['reason', 'Failure']
    assert [s['kind'] for s in failure['methodFlow']['steps']] == ['call', 'call', 'throw']
    assert failure['documentation']['summary'] == ''


def test_secondary_constructor_delegation(tmp_path):
    """Delegation arguments and the constructor body keep their source execution order."""
    source = FIXTURE.read_text()
    result = describe(tmp_path, source, 'constructor', 38, kind='constructor', callableFacts={'kind': 'constructor', 'declaringType': 'Reader'})
    assert calls(result) == ['seed', 'this', 'setup']
    assert result['parameters'] == [{'name': 'value', 'type': 'Int'}]
    assert result['returnType'] == ''
    assert result['events'][1]['sourceExpression'] == 'this(seed(value))'


def test_when_literals_and_ordered_predicates(tmp_path):
    """A later when predicate executes only after earlier alternatives fail."""
    result = describe(tmp_path, 'fun run() = when(code()) { 1 -> one(); 2,3 -> two(); else -> "other" }')
    assert calls(result) == ['code', 'one', 'two']
    first = result['methodFlow']['steps'][1]
    assert first['controlKind'] == 'when'
    assert first['arms'][0]['steps'][0]['label'] == 'one()'
    second = first['arms'][1]['steps'][0]
    assert second['arms'][0]['steps'][0]['label'] == 'two()'
    assert second['arms'][1]['steps'][0]['expression'] == '"other"'
    assert not result['methodFlow']['unsupported']
    predicates = describe(tmp_path, 'fun run() { when { first() -> one(); second() -> two(); else -> other() } }')
    assert calls(predicates) == ['first', 'one', 'second', 'two', 'other']
    first_event, _, second_event, *_ = predicates['events']
    assert not first_event['conditional'] and second_event['conditional']
    assert second_event['branchIds'][-1].endswith('-a1')


def test_safe_calls_elvis_short_circuit_and_chains(tmp_path):
    """Nullable and boolean alternatives guard both call receivers and arguments."""
    result = describe(tmp_path, 'fun run() { val a=get()?.one(arg())?.two(next()) ?: fallback(); val b=ok && check(); val c=ok || check(); val d=obj?.field; normal() }')
    assert calls(result) == ['get', 'arg', 'one', 'next', 'two', 'fallback', 'check', 'check', 'normal']
    assert [e['conditional'] for e in result['events']] == [False, True, True, True, True, True, True, True, False]
    branches = [s for s in result['methodFlow']['steps'] if s['kind'] == 'branch']
    assert [s['controlKind'] for s in branches] == ['?.', '?.', '?:', '&&', '||', '?.']
    assert branches[0]['arms'][0]['steps'][0]['label'] == 'arg()'
    assert branches[2]['arms'][1]['steps'][0]['reuseResult']
    assert branches[3]['arms'][1]['emptyReason'] == branches[4]['arms'][1]['emptyReason'] == 'skip'
    assert branches[-1]['arms'][0]['steps'][0]['expression'] == 'obj?.field'


def test_loops_condition_ownership_and_empty_arms(tmp_path):
    """Iterables execute once; repeated tests and jumps belong to their own loop."""
    result = describe(tmp_path, 'fun run() { for(x in load()) { if(skip()) continue; if(done()) break; work() }; while(test()) body(); do { once() } while(last()); if(ok){} }')
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'loop', 'loop', 'loop', 'branch']
    assert calls(result) == ['load', 'skip', 'done', 'work', 'test', 'body', 'once', 'last']
    assert steps[1]['condition'] == []
    assert steps[2]['condition'][0]['label'] == 'test()'
    assert steps[3]['controlKind'] == 'do'
    assert steps[3]['condition'][0]['label'] == 'last()'
    assert all(a['steps'] == [] and a['emptyReason'] == 'skip' for a in steps[-1]['arms'])
    assert not result['methodFlow']['unsupported']


def test_trailing_lambda_is_one_invocation_and_body_stays_opaque(tmp_path):
    """Coroutine/scope builders have one invocation without claiming their lambda schedule."""
    result = describe(tmp_path, 'suspend fun run() { launch(ctx()) { hidden() }; x?.let(arg()) { forbidden() }; val f=fun(){ nested() }; receive(); job.await() }')
    assert calls(result) == ['ctx', 'launch', 'arg', 'let', 'receive', 'await']
    assert result['events'][2]['conditional'] and result['events'][3]['conditional']
    assert result['events'][1]['sourceExpression'] == 'launch(ctx()) { hidden() }'
    assert all(not e['awaited'] and not e['parallel'] for e in result['events'])


@pytest.mark.parametrize('body,reason', [
    ('try { return work() } finally { cleanup() }', 'try_expression'),
    ('val x=object {\n init { hidden() }\n }', 'object_literal'),
    ('outer@ while(ok) { break@outer }', 'labeled loop'),
    ('return@run hidden()', 'labeled jumps'),
    ('val x=when(n) { first(), second() -> hidden(); else -> other() }', 'comma conditions'),
    ('obj?.field = hidden()', 'safe-access assignment'),
    ('foo<Int>(hidden())', 'generic-call/comparison'),
])
def test_unsupported_paths_are_visible_without_false_calls(tmp_path, body, reason):
    """Unsupported control syntax has an explicit boundary instead of fabricated calls."""
    result = describe(tmp_path, 'fun run() { ' + body + ' }')
    assert calls(result) == []
    assert any(reason in r for r in result['methodFlow']['unsupported'])
    assert any(s.get('incomplete') for s in flatten(result['methodFlow']['steps']))


def test_duplicate_names_owner_lines_and_negative_selection(tmp_path):
    """An exact declaration is required across owners, stale lines and nested names."""
    source = 'class A {\n fun run(){ one() }\n}\nclass B {\n fun run(){ two() }\n}\n'
    assert calls(describe(tmp_path, source, line=5, callableFacts={'declaringType': 'B'})) == ['two']
    for node in [{'label': 'run()'}, {'label': 'run()', 'line': 1}, {'label': 'run()', 'line': 2, 'callableFacts': {'declaringType': 'B'}}, {'label': 'missing()', 'line': 2}]:
        result = analyze({'text': source}, node, max_events=160)
        assert result['sourceStatus'] == 'unresolved'
        assert not result['events'] and not result['methodFlow']['steps']
    local = 'fun run() {\n fun run(){ hidden() }\n outer()\n}'
    result = describe(tmp_path, local, line=1)
    assert calls(result) == ['outer']
    nested = describe(tmp_path, local, line=2)
    assert calls(nested) == ['hidden']


def test_parse_error_and_declaration_only(tmp_path):
    """Malformed syntax never borrows another body; interface declarations remain declaration-only."""
    result = describe(tmp_path, 'fun run() { if( { wrong() }')
    assert result['sourceStatus'] == 'unresolved' and not result['events']
    declaration = describe(tmp_path, 'interface C {\n fun run(x: Int): String\n}', line=2)
    assert declaration['methodFlow']['status'] == 'declaration-only'
    assert declaration['parameters'] == [{'name': 'x', 'type': 'Int'}]
    assert declaration['returnType'] == 'String'


def test_unicode_sites_repetition_and_raw_graph_binding(tmp_path):
    """Byte-accurate source occurrences bind only to existing graph targets without mutation."""
    source = 'fun run() { val привет="мир"; target(arg()); target(arg()) }'
    path = tmp_path / 'Code.kt'
    path.write_text(source)
    node = {'id': 'caller-raw', 'label': 'run()', 'file': 'Code.kt', 'line': 1}
    graph = {'nodes': [node, {'id': 'target-raw', 'label': 'target()', 'kind': 'external'}],
             'edges': [{'source': 'caller-raw', 'target': 'target-raw', 'relation': 'calls', 'source_file': 'Code.kt', 'source_location': 'L1', 'confidence': 'EXTRACTED'}]}
    before = deepcopy(graph)
    result = build_node_description(tmp_path, graph, 'caller-raw', 's')
    assert graph == before and path.read_text() == source
    assert calls(result) == ['arg', 'target', 'arg', 'target']
    assert [e['targetNodeIds'] for e in result['events']] == [[], ['target-raw'], [], ['target-raw']]
    content = source.encode()
    for event in result['events']:
        assert content[event['column'] - 1:event['endColumn'] - 1].decode() == event['sourceExpression']
    assert result['events'][1]['column'] != result['events'][3]['column']


def test_budgets_and_unavailable_grammar(monkeypatch):
    """AST, depth, steps and event caps remain visible and the optional grammar fails honestly."""
    many = 'fun run(){' + ';'.join('call()' for _ in range(300)) + '}'
    result = analyze({'text': many}, {'label': 'run()', 'line': 1}, max_events=2)
    assert len(result['events']) == 2 and result['eventsTruncated']
    assert result['methodFlow']['truncated']
    assert len(flatten(result['methodFlow']['steps'])) <= 180
    deep = 'fun run() = ' + 'wrap(' * 80 + 'leaf()' + ')' * 80
    result = analyze({'text': deep}, {'label': 'run()', 'line': 1}, max_events=160)
    assert result['methodFlow']['truncated']
    giant = 'fun run(){' + ';'.join('call()' for _ in range(5000)) + '}'
    result = analyze({'text': giant}, {'label': 'run()', 'line': 1}, max_events=160)
    assert result['sourceStatus'] == 'unresolved' and result['methodFlow']['truncated']
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        """Simulate a missing local optional grammar without installing dependencies."""
        if name == 'tree_sitter_kotlin':
            raise ImportError('missing grammar')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    result = analyze({'text': 'fun run(){ work() }'}, {'label': 'run()'}, max_events=160)
    assert result['sourceStatus'] == 'unavailable' and not result['events']


def test_real_member_capture_graph_preserves_raw_ids(tmp_path):
    """Production member enrichment and description retain the selected upstream raw ID."""
    from atlas.member_graph import enrich_member_graph
    source = 'class Reader {\n fun run(){ outer(inner()) }\n fun outer(x:Int){}\n fun inner() = 1\n}'
    path = tmp_path / 'Code.kt'
    path.write_text(source)
    graph = {'nodes': [{'id': 'raw-file', 'label': 'Code.kt', 'kind': 'file', 'file': 'Code.kt', 'line': 1},
                       {'id': 'raw-run', 'label': 'run()', 'kind': 'symbol', 'file': 'Code.kt', 'line': 2}], 'edges': []}
    graph = enrich_member_graph(graph, tmp_path)
    assert next(n for n in graph['nodes'] if n.get('memberFacts', {}).get('name') == 'run')['id'] == 'raw-run'
    before = deepcopy(graph)
    result = build_node_description(tmp_path, graph, 'raw-run', 's')
    assert calls(result) == ['inner', 'outer']
    assert graph == before and path.read_text() == source


def test_value_only_blocks_type_matches_and_condition_values(tmp_path):
    """Block result expressions and type/range matching remain visible alternatives."""
    result = describe(tmp_path, 'fun run(){ val x=if(ok){ log(); 1 }else{ val temp=2; obj.name }; val y=when(x){ is T -> "type"; in range() -> 2; else -> 3 } }')
    branches = [s for s in result['methodFlow']['steps'] if s['kind'] == 'branch']
    assert branches[0]['arms'][0]['steps'][-1]['expression'] == '1'
    assert branches[0]['arms'][1]['steps'][-1]['expression'] == 'obj.name'
    assert calls(result) == ['log', 'range']
    assert result['events'][1]['conditional']
    assert not result['methodFlow']['unsupported']


def test_same_line_overloads_need_parameter_identity_and_class_nodes_are_excluded():
    """Same-line declarations cannot be selected by their name alone."""
    source = 'fun run(x:Int){ one() }; fun run(x:String){ two() }'
    ambiguous = analyze({'text': source}, {'label': 'run()', 'line': 1}, max_events=160)
    assert ambiguous['sourceStatus'] == 'unresolved'
    exact = analyze({'text': source}, {'label': 'run()', 'line': 1, 'callableFacts': {'parameterList': '(x:String)'}}, max_events=160)
    assert calls(exact) == ['two']
    class_result = analyze({'text': 'class C\nfun C(){ wrong() }'}, {'label': 'C', 'kind': 'class', 'line': 1}, max_events=160)
    assert class_result['sourceStatus'] == 'unavailable' and not class_result['events']


def test_negative_event_budget_and_bounded_labels_and_documentation():
    """All user-facing source text stays bounded and absent events cannot create unbound call rows."""
    source = '/**' + 'd' * 9000 + '*/\nfun run(){ ' + 'long' * 150 + '() }'
    result = analyze({'text': source}, {'label': 'run()', 'line': 2}, max_events=-1)
    assert not result['events'] and result['eventsTruncated']
    assert all(len(s['label']) <= 400 for s in flatten(result['methodFlow']['steps']))
    assert all(s['kind'] != 'call' for s in flatten(result['methodFlow']['steps']))
    assert len(result['documentation']['summary']) <= 8000
    assert result['documentation']['truncated']


def test_host_read_budgets_are_enforced(tmp_path):
    """The language adapter inherits file/byte caps and never reads source independently."""
    path = tmp_path / 'Code.kt'
    path.write_text('fun run(){ work() }')
    node = {'id': 'run', 'label': 'run()', 'line': 1, 'file': 'Code.kt'}
    for files, byte_cap, flag in [(0, 1000, 'files'), (1, 1, 'bytes')]:
        analysis = SourceAnalysis(tmp_path, 's', files, byte_cap, 160)
        result = analysis.describe(node)
        assert result['sourceStatus'] == 'unavailable' and not result['events']
        assert analysis.truncation[flag]


def test_defaults_are_not_body_calls_and_true_loop_can_break(tmp_path):
    """Defaults and lambda bodies remain outside execution; local break is preserved in true loops."""
    source = 'suspend fun run(x:Int=default(), cb:()->Unit={hidden()}):Int { while(true){ if(done()) break; work() }; return x }'
    result = describe(tmp_path, source)
    assert calls(result) == ['done', 'work']
    assert result['parameters'] == [{'name': 'x', 'type': 'Int'}, {'name': 'cb', 'type': '()->Unit'}]
    loop = result['methodFlow']['steps'][0]
    assert loop['canExit'] is False
    assert loop['body'][1]['arms'][0]['steps'][0]['kind'] == 'break'
    assert result['methodFlow']['steps'][-1]['kind'] == 'return'


def test_generic_supported_ast_and_ambiguous_ast_are_distinct(tmp_path):
    """Only an actual grammar call is reported; ambiguous comparison-shaped syntax is opaque."""
    result = describe(tmp_path, 'fun run(){ foo<Int>(); x.foo<Int>(hidden()) }')
    assert calls(result) == ['foo']
    assert result['methodFlow']['steps'][-1]['kind'] == 'unknown'
    assert any('generic-call/comparison' in r for r in result['methodFlow']['unsupported'])


def test_opaque_terminal_paths_stop_and_deferred_declarations_continue(tmp_path):
    """Unknown control boundaries stop UI flow, while merely creating a closure can continue."""
    result = describe(tmp_path, 'fun run(){ val f={hidden()}; after(); try {throw Boom()} finally {clean()}; later() }')
    steps = result['methodFlow']['steps']
    assert steps[0]['kind'] == 'statement' and steps[0]['incomplete']
    assert steps[2]['label'] == 'after()'
    assert steps[3]['kind'] == 'unknown' and steps[3]['incomplete']
    assert calls(result) == ['after', 'later']


def test_empty_loop_bodies_and_no_else_skip(tmp_path):
    """Empty bodies are actual skips and do not become missing-source warnings."""
    result = describe(tmp_path, 'fun run(){ for(x in items()); while(ok); do ; while(ok); if(ok){} }')
    loops = [s for s in result['methodFlow']['steps'] if s['kind'] == 'loop']
    assert len(loops) == 3
    assert all(s['body'] == [] and s['bodyEmptyReason'] == 'skip' for s in loops)
    assert calls(result) == ['items']
    assert not result['methodFlow']['unsupported']


def test_control_ids_are_scoped_to_selected_raw_node():
    """Equal source offsets in different methods cannot alias branch or loop contexts."""
    source = {'text': 'fun run(){ if(ok){ one() }; for(x in items()){ work() } }'}
    ids = []
    for raw_id in ('first-raw', 'second-raw'):
        result = analyze(source, {'id': raw_id, 'label': 'run()', 'line': 1}, max_events=160)
        arm_ids = {arm['id'] for branch in result['controlFlow']['branches'] for arm in branch['arms']}
        assert all(set(e['branchIds']) <= arm_ids for e in result['events'])
        ids.append(arm_ids)
    assert not ids[0] & ids[1]
