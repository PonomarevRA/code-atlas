"""Contract tests through the real safe host and node description; Swift is never executed."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.swift import analyze
from atlas.node_description import build_node_description

FIXTURES = Path(__file__).parent / 'fixtures' / 'flow' / 'swift'


def describe(tmp_path, body, *, name='run', max_events=160):
    """Exercise production source reading for an exact method in a temporary Swift fixture."""
    source = 'struct C {\nfunc ' + name + '() async throws {\n' + body + '\n}\n}\n'
    (tmp_path / 'Reader.swift').write_text(source)
    node = {'id': 'raw-run', 'label': name + '()', 'file': 'Reader.swift', 'line': 2,
            'memberFacts': {'language': 'swift', 'kind': 'method', 'name': name}}
    result = SourceAnalysis(tmp_path, 'snapshot', 3, 1024 * 1024, max_events).describe(node)
    assert result['sourceStatus'] == 'parsed', result['unresolved']
    return result


def all_steps(steps):
    """Walk tree structure only, keeping test assertions independent of event extraction."""
    result = []
    for step in steps:
        result.append(step)
        for arm in step.get('arms', []):
            result.extend(all_steps(arm['steps']))
        for key in ('condition', 'body', 'update'):
            result.extend(all_steps(step.get(key, [])))
    return result


def names(result):
    """Return static lexical evaluation order, excluding deferred bodies."""
    return [event['callee'] for event in result['events']]


def test_fixture_signature_docs_exact_tree_and_real_binding(tmp_path):
    """A raw graph survives describe/build unchanged; host alone binds existing graph targets."""
    text = (FIXTURES / 'Reader.swift').read_text()
    (tmp_path / 'Reader.swift').write_text(text)
    node = {'id': 'upstream/raw/swift-run', 'label': 'run()', 'file': 'Reader.swift', 'line': 4,
            'memberFacts': {'language': 'swift', 'name': 'run'}}
    target = {'id': 'upstream/raw/prepare', 'label': 'prepare()', 'kind': 'external'}
    graph = {'nodes': [node, target], 'edges': [{'source': node['id'], 'target': target['id'],
              'relation': 'calls', 'source_location': 'L5', 'confidence': 'EXTRACTED'}]}
    before = deepcopy(graph)
    source_before = (tmp_path / 'Reader.swift').read_bytes()
    result = build_node_description(tmp_path, graph, node['id'], 'fixture-snapshot')
    assert graph == before
    assert (tmp_path / 'Reader.swift').read_bytes() == source_before
    assert result['nodeId'] == node['id'] and result['snapshotId'] == 'fixture-snapshot'
    assert result['sourceHash'] == sha256(source_before).hexdigest()
    assert result['documentation']['summary'] == 'Read the selected value.'
    assert result['parameters'] == [{'name': 'input', 'type': 'Int'}, {'name': 'name', 'type': 'String'}]
    assert result['returnType'] == 'String'
    assert result['events'][0]['targetNodeIds'] == [target['id']]
    assert all(set(e['targetNodeIds']) <= {target['id']} for e in result['events'])
    assert names(result) == ['prepare', 'inner', 'other', 'wrap', 'ready', 'permitted', 'yes', 'no',
                             'items', 'skip', 'work', 'done', 'more', 'tick', 'finish']
    steps = result['methodFlow']['steps']
    assert [s['kind'] for s in steps] == ['call', 'call', 'call', 'call', 'statement', 'call',
                                         'branch', 'branch', 'call', 'loop', 'loop', 'call', 'await', 'return']
    assert result['events'][-1]['awaited'] and not result['events'][-1]['parallel']
    assert result['parameterFacts']['status'] == result['attributeFacts']['status'] == 'unavailable'
    tree_sites = [s['site'] for s in all_steps(steps) if s['kind'] == 'call']
    assert len(tree_sites) == len(result['events'])
    assert all({k: event[k] for k in ('line', 'column', 'endLine', 'endColumn')} in tree_sites for event in result['events'])


def test_nested_arguments_repeated_calls_await_and_byte_coordinates(tmp_path):
    """Receiver precedes arguments; only the directly awaited containing call owns await."""
    result = describe(tmp_path, 'let привет = 1; same(); same(); factory().send(inner()); return try await finish(inner2())')
    assert names(result) == ['same', 'same', 'factory', 'inner', 'send', 'inner2', 'finish']
    assert [event['awaited'] for event in result['events']] == [False, False, False, False, False, False, True]
    events = result['events']
    assert events[0]['column'] == len('let привет = 1; '.encode()) + 1
    assert events[0]['column'] != events[1]['column']
    assert [s['kind'] for s in result['methodFlow']['steps']][-3:] == ['call', 'await', 'return']


def test_guard_failure_order_and_success_continuation(tmp_path):
    """Else exits cannot become predecessors of successful continuation calls."""
    result = describe(tmp_path, 'before(); guard let x = read(), valid(x) else { return reject() }; use(x); after()')
    assert names(result) == ['before', 'read', 'valid', 'reject', 'use', 'after']
    choice = result['methodFlow']['steps'][-1]
    assert choice['controlKind'] == 'guard'
    assert [s['label'] for s in choice['arms'][0]['steps']] == ['use()', 'after()']
    assert [s['kind'] for s in choice['arms'][1]['steps']] == ['call', 'return']
    assert all(e['conditional'] for e in result['events'] if e['callee'] in {'valid', 'reject', 'use', 'after'})
    assert result['events'][2]['branchIds']
    ids = {a['id'] for b in result['controlFlow']['branches'] for a in b['arms']}
    assert all(set(e['branchIds']) <= ids for e in result['events'])


def test_if_elseif_and_comma_binding_conditions(tmp_path):
    """Conditions evaluate once and subsequent comma conditions remain conditional."""
    result = describe(tmp_path, 'if let x = load(), check(x) { yes() } else if other() { alternate() } else { no() }')
    assert names(result) == ['load', 'check', 'yes', 'other', 'alternate', 'no']
    assert not result['events'][0]['conditional']
    assert all(e['conditional'] for e in result['events'][1:])
    choices = [s for s in all_steps(result['methodFlow']['steps']) if s.get('controlKind') == 'if']
    assert len(choices) == 2
    assert choices[1] in choices[0]['arms'][1]['steps']


def test_optional_coalescing_short_circuit_and_ternary(tmp_path):
    """Neither optional arguments nor lazy alternatives are unconditional top-level calls."""
    result = describe(tmp_path, 'let x = obj?.go(arg()) ?? fallback(); let y = left() || right(); return ready() ? one() : two()')
    assert names(result) == ['arg', 'go', 'fallback', 'left', 'right', 'ready', 'one', 'two']
    assert [e['conditional'] for e in result['events']] == [True, True, True, False, True, False, True, True]
    branches = [s for s in all_steps(result['methodFlow']['steps']) if s['kind'] == 'branch']
    assert [s['controlKind'] for s in branches] == ['?.', '??', '||', 'conditional']
    assert branches[0]['arms'][1]['emptyReason'] == 'skip'
    assert branches[1]['arms'][1]['steps'][0]['reuseResult']
    assert [a['steps'][0]['label'] for a in branches[-1]['arms']] == ['one()', 'two()']
    assert not result['methodFlow']['unsupported']


def test_literal_alternatives_and_empty_real_arms(tmp_path):
    """Value-only outcomes are retained, and real empty branches are labelled skip."""
    result = describe(tmp_path, 'let x = value ?? 10; let y = ok ? 1 : 2; if ok {}')
    branches = [s for s in result['methodFlow']['steps'] if s['kind'] == 'branch']
    assert branches[0]['arms'][0]['steps'][0]['expression'] == '10'
    assert [arm['steps'][0]['expression'] for arm in branches[1]['arms']] == ['1', '2']
    assert all(arm['emptyReason'] == 'skip' for arm in branches[2]['arms'])


def test_loops_jumps_switch_and_terminals(tmp_path):
    """Collections evaluate once; explicit jumps never become fallthrough in their sequence."""
    result = describe(tmp_path, 'for x in items() { if skip(x) { continue }; work(x); break; dead() }; while more() { tick() }; switch tag() { case 1: one(); case 2: throw bad(); default: return done() }; after()')
    assert names(result) == ['items', 'skip', 'work', 'more', 'tick', 'tag', 'one', 'bad', 'done', 'after']
    steps = result['methodFlow']['steps']
    assert steps[1]['controlKind'] == 'foreach' and not steps[1]['condition']
    assert steps[2]['controlKind'] == 'while' and steps[2]['condition'][0]['label'] == 'more()'
    switch = steps[4]
    assert switch['controlKind'] == 'switch' and len(switch['arms']) == 3
    assert [a['steps'][-1]['kind'] for a in switch['arms']] == ['call', 'throw', 'return']
    assert {t['kind'] for t in result['controlFlow']['terminals']} == {'continue', 'break', 'throw', 'return'}


def test_defer_closure_local_function_and_capture_are_boundaries(tmp_path):
    """Deferred/cleanup code cannot leak into the containing method's lexical call list."""
    result = describe(tmp_path, 'defer { cleanup() }; let f = { [x = capture()] in hidden(x) }; func local() { nested() }; register { callback() }; after()')
    assert names(result) == ['register', 'after']
    reasons = '\n'.join(result['methodFlow']['unsupported'])
    assert 'scope exit' in reasons and 'capture' in reasons and 'local declaration' in reasons
    assert any(s.get('incomplete') for s in result['methodFlow']['steps'])
    assert not any(e['callee'] == 'defer' for e in result['events'])


@pytest.mark.parametrize('body, reason', [
    ('do { try work() } catch { rescue() }; after()', 'exception'),
    ('async let x = load(); after()', 'async let'),
    ('for await x in stream() { work(x) }; after()', 'asynchronous'),
    ('switch x { case 1 where valid(): work(); default: break }; after()', 'where/fallthrough'),
    ('switch x { case 1: fallthrough; default: work() }; after()', 'where/fallthrough'),
    ('let x = try? work(); after()', 'try?/try!'),
    ('let x = try! work(); after()', 'try?/try!'),
    ('let x = obj?.one().two(arg()); after()', 'compound optional'),
    ('let x = obj?.property; after()', 'optional property'),
    ('await factory().send(inner()); after()', 'await inside receiver'),
    ('let x = #macro(hidden()); after()', 'macro/ownership'),
    ('let x = consume value; after()', 'macro/ownership'),
])
def test_unsupported_compounds_are_visible_without_false_body_calls(tmp_path, body, reason):
    """A partial adapter must disclose its boundary and withhold unsafe flattened calls."""
    result = describe(tmp_path, body)
    assert names(result) == ['after']
    assert any(reason in s for s in result['methodFlow']['unsupported'])
    assert any(s.get('incomplete') for s in all_steps(result['methodFlow']['steps']))


def test_init_and_own_documentation_only(tmp_path):
    """Constructor selection and undocumented methods do not inherit type comments."""
    text = '/// TYPE\nstruct C {\n init(x: Int) { configure(x) }\n func run() { work() }\n}\n'
    (tmp_path / 'Reader.swift').write_text(text)
    analysis = SourceAnalysis(tmp_path, 's', 2, 10000, 100)
    init = analysis.describe({'id': 'raw-init', 'label': 'init()', 'file': 'Reader.swift', 'line': 3})
    method = analysis.describe({'id': 'raw-run', 'label': 'run()', 'file': 'Reader.swift', 'line': 4})
    assert init['sourceStatus'] == method['sourceStatus'] == 'parsed'
    assert init['parameters'] == [{'name': 'x', 'type': 'Int'}]
    assert names(init) == ['configure'] and names(method) == ['work']
    assert init['documentation']['summary'] == method['documentation']['summary'] == ''


@pytest.mark.parametrize('node', [
    {'label': 'f()'}, {'label': 'f()', 'line': 3}, {'label': 'missing()', 'line': 1},
    {'label': 'f()', 'line': 1, 'endLine': 99},
])
def test_missing_ambiguous_or_stale_identity_does_not_borrow_neighbour(node):
    """Same-name owner/overload ambiguity and stale ranges remain unresolved."""
    text = 'struct A { func f(){ wrongA() } }\nstruct B { func f(){ wrongB() } }'
    result = analyze({'text': text}, node, max_events=10)
    assert result['sourceStatus'] == 'unresolved'
    assert not result['events'] and not result['methodFlow']['steps']
    assert result['unresolved']


def test_exact_nested_function_selected_separately():
    """A local declaration has its own body only when the exact local raw node was selected."""
    text = 'func outer() {\n func inner() { hidden() }\n outerCall()\n}'
    outer = analyze({'text': text}, {'label': 'outer()', 'line': 1}, max_events=30)
    inner = analyze({'text': text}, {'label': 'inner()', 'line': 2}, max_events=30)
    assert names(outer) == ['outerCall'] and names(inner) == ['hidden']


def test_parse_error_is_explicit_and_does_not_publish_a_partial_tree():
    """Malformed syntax is not represented as supported static semantics."""
    result = analyze({'text': 'func f() { if { work() } }'}, {'label': 'f()', 'line': 1}, max_events=10)
    assert result['sourceStatus'] == 'unresolved' and not result['events']
    assert any('parse errors' in reason for reason in result['unresolved'])


def test_missing_grammar_is_honest(monkeypatch):
    """Installed dependencies are inspected locally; a missing grammar is never downloaded."""
    monkeypatch.setitem(sys.modules, 'tree_sitter_swift', None)
    result = analyze({'text': 'func f(){work()}'}, {'label': 'f()', 'line': 1}, max_events=10)
    assert result['sourceStatus'] == 'unavailable' and not result['events']
    assert any('grammar unavailable' in reason for reason in result['unresolved'])


@pytest.mark.parametrize('limit', [0, 1, 2, 5])
def test_event_budget_keeps_tree_and_events_consistent(tmp_path, limit):
    """A call absent from bounded events cannot pretend to have a bound tree occurrence."""
    result = describe(tmp_path, 'outer(inner(), other()); last()', max_events=limit)
    assert len(result['events']) <= limit
    assert result['eventsTruncated'] == (limit < 4)
    sites = [s['site'] for s in all_steps(result['methodFlow']['steps']) if s['kind'] == 'call']
    assert len(sites) == len(result['events'])


def test_step_and_depth_limits_keep_call_sites_in_the_visible_tree(tmp_path, monkeypatch):
    """Container steps need reserved budget; truncated arms must not leak orphan call events."""
    monkeypatch.setattr('atlas.flow_languages.swift_support.flow.MAX_STEPS', 6)
    result = describe(tmp_path, 'if yes() { a(); b(); c(); d() } else { e() }; last()')
    flat = all_steps(result['methodFlow']['steps'])
    assert result['methodFlow']['truncated']
    assert len(flat) <= 6
    assert len([s for s in flat if s['kind'] == 'call']) == len(result['events'])
    monkeypatch.setattr('atlas.flow_languages.swift_support.flow.MAX_DEPTH', 5)
    limited = describe(tmp_path, 'outer(inner(deep()))')
    assert limited['methodFlow']['truncated'] and limited['methodFlow']['unsupported']


def test_ast_and_label_budgets_are_explicit(tmp_path):
    """Large ASTs and labels cannot silently masquerade as complete source extraction."""
    large = analyze({'text': 'func f(){' + 'work();' * 6000 + '}'}, {'label': 'f()', 'line': 1}, max_events=10)
    assert large['sourceStatus'] == 'unresolved' and not large['events']
    assert any('20000' in reason for reason in large['unresolved'])
    result = describe(tmp_path, 'let value = "' + 'x' * 600 + '"; after()')
    assert any('400' in reason for reason in result['unresolved'])
    assert all(len(s['label']) <= 400 for s in all_steps(result['methodFlow']['steps']))


def test_host_file_byte_budgets_and_symlink_boundary(tmp_path):
    """The adapter cannot bypass the existing host's safe reader or per-request budgets."""
    (tmp_path / 'Reader.swift').write_text('func run(){ work() }')
    node = {'id': 'raw', 'label': 'run()', 'line': 1, 'file': 'Reader.swift'}
    assert SourceAnalysis(tmp_path, 's', 0, 1000, 10).describe(node)['sourceStatus'] == 'unavailable'
    assert SourceAnalysis(tmp_path, 's', 1, 1, 10).describe(node)['sourceStatus'] == 'unavailable'
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'project').mkdir()
    (tmp_path / 'outside' / 'Secret.swift').write_text('func run(){ secret() }')
    (tmp_path / 'project' / 'Reader.swift').symlink_to(tmp_path / 'outside' / 'Secret.swift')
    result = SourceAnalysis(tmp_path / 'project', 's', 1, 1000, 10).describe(node)
    assert result['sourceStatus'] == 'unavailable' and not result['events']


def test_empty_true_arm_does_not_steal_else_body(tmp_path):
    """Swift omits empty statements nodes, so arm ownership must follow the else token."""
    result = describe(tmp_path, 'if ok {} else { no() }; if ready {} else if other() { yes() }')
    branches = [s for s in result['methodFlow']['steps'] if s['kind'] == 'branch']
    assert branches[0]['arms'][0] == {'label': 'true', 'steps': [], 'emptyReason': 'skip'}
    assert branches[0]['arms'][1]['steps'][0]['label'] == 'no()'
    assert branches[1]['arms'][0]['emptyReason'] == 'skip'
    assert branches[1]['arms'][1]['steps'][-1]['controlKind'] == 'if'


def test_multiple_bindings_and_simple_assignments_keep_all_initializers(tmp_path):
    """Repeated value fields must not discard second or later lexical initializers."""
    result = describe(tmp_path, 'let a = first(), b = second(); var c = third(), d = fourth(); c = update()')
    assert names(result) == ['first', 'second', 'third', 'fourth', 'update']
    assert [s['label'] for s in result['methodFlow']['steps'] if s['kind'] == 'statement'] == [
        'let a = first(), b = second()', 'var c = third(), d = fourth()', 'c = update()']


def test_compile_time_alternatives_are_not_runtime_linear_calls(tmp_path):
    """Conditional compilation directives are siblings in this grammar, not owning AST nodes."""
    result = describe(tmp_path, 'before()\n#if DEBUG\n debug()\n #if EXTRA\n extra()\n #endif\n#else\n release()\n#endif\nafter()')
    assert names(result) == ['before', 'after']
    assert any('conditional compilation' in reason for reason in result['methodFlow']['unsupported'])


def test_subscript_is_not_a_function_call_and_complex_setter_is_boundary(tmp_path):
    """The grammar calls subscripts call_expression; only explicit index calls are call events."""
    result = describe(tmp_path, 'let x = values[index()]; values[index2()] = rhs(); after()')
    assert names(result) == ['index', 'after']
    assert any('assignment target' in reason for reason in result['methodFlow']['unsupported'])
    assert not any(e['callee'] == 'values' for e in result['events'])


def test_case_patterns_do_not_turn_matching_into_calls(tmp_path):
    """Pattern syntax must not be flattened as normal expression evaluation."""
    result = describe(tmp_path, 'if case .some(let x) = load() { use(x) }; for case let .some(y) in items() { work(y) }; after()')
    assert names(result) == ['use', 'after']
    assert result['events'][0]['conditional']
    assert any('case pattern' in reason for reason in result['methodFlow']['unsupported'])


def test_real_swift_member_map_reuses_upstream_method_id(tmp_path):
    """Real source-member enrichment feeds the production flow endpoint without a map rebuild."""
    from atlas.member_graph import enrich_member_graph
    text = 'struct C {\n func run(){ before(); if ready() { work() } }\n}\n'
    (tmp_path / 'Reader.swift').write_text(text)
    raw = {'nodes': [{'id': 'original-graphify-id', 'label': 'run()', 'kind': 'symbol',
                      'file': 'Reader.swift', 'line': 2}], 'edges': [], 'metadata': {}}
    original = deepcopy(raw)
    graph = enrich_member_graph(raw, tmp_path)
    assert raw == original
    node = next(n for n in graph['nodes'] if n['id'] == 'original-graphify-id')
    assert node['memberFacts']['language'] == 'swift'
    captured = deepcopy(graph)
    result = build_node_description(tmp_path, graph, node['id'], 'real-map-fixture')
    assert result['sourceStatus'] == 'parsed' and names(result) == ['before', 'ready', 'work']
    assert graph == captured and (tmp_path / 'Reader.swift').read_text() == text


def test_parenthesized_await_and_return_without_whitespace(tmp_path):
    """Token identity, rather than whitespace, distinguishes a terminal from its operand."""
    result = describe(tmp_path, 'return(await (finish(inner()))); dead()')
    assert names(result) == ['inner', 'finish']
    assert [e['awaited'] for e in result['events']] == [False, True]
    assert result['methodFlow']['steps'][-1]['kind'] == 'return'


def test_real_empty_arm_stays_skip_after_other_arm_budget_truncation(tmp_path, monkeypatch):
    """A known empty source branch cannot become not-extracted because a sibling exceeded budget."""
    monkeypatch.setattr('atlas.flow_languages.swift_support.flow.MAX_STEPS', 3)
    result = describe(tmp_path, 'if ready() { a(); b(); c() }')
    assert result['methodFlow']['truncated']
    branch = result['methodFlow']['steps'][1]
    assert branch['arms'][1]['emptyReason'] == 'skip'
    assert result['eventsTruncated']


def test_deferred_fixture_across_guard_throw_exit(tmp_path):
    """Cleanup and callback registration stay visible but neither body becomes an immediate call."""
    (tmp_path / 'Cleanup.swift').write_bytes((FIXTURES / 'Cleanup.swift').read_bytes())
    node = {'id': 'cleanup-run', 'label': 'run()', 'line': 3, 'file': 'Cleanup.swift'}
    result = build_node_description(tmp_path, {'nodes': [node], 'edges': []}, node['id'], 'cleanup-fixture')
    assert names(result) == ['read', 'makeError', 'use', 'flush']
    assert result['methodFlow']['steps'][0]['label'].startswith('Регистрация defer')
    guard = result['methodFlow']['steps'][-1]
    assert guard['arms'][1]['steps'][-1]['kind'] == 'throw'
    assert not any(e['callee'] in {'cleanup', 'hidden', 'defer'} for e in result['events'])
    assert result['events'][-1]['awaited'] and result['events'][-1]['conditional']


def test_duplicate_owner_and_overload_selection_requires_exact_start():
    """An exact distinct line identifies one owner; same-line overload ambiguity remains honest."""
    text = 'struct A { func f(){ wrong() } }\nstruct B { func f(){ selected() } }'
    result = analyze({'text': text}, {'id': 'raw-B-f', 'label': 'f()', 'line': 2}, max_events=10)
    assert result['sourceStatus'] == 'parsed' and names(result) == ['selected']
    ambiguous = analyze({'text': 'func f(_ x:Int){one()}; func f(_ x:String){two()}'},
                        {'label': 'f()', 'line': 1}, max_events=10)
    assert ambiguous['sourceStatus'] == 'unresolved' and not ambiguous['events']


def test_protocol_declaration_has_no_invented_executable_body():
    """An abstract requirement remains declaration-only, never an empty successful execution."""
    result = analyze({'text': 'protocol P { func run(_ x: Int) -> Int }'},
                     {'label': 'run()', 'line': 1}, max_events=10)
    assert result['sourceStatus'] == 'parsed'
    assert result['methodFlow']['status'] == 'declaration-only'
    assert not result['events'] and not result['methodFlow']['steps']


def test_repeat_loop_and_for_where_preserve_evaluation_phase(tmp_path):
    """Repeat tests after its body; for-where collection is once and its predicate is per element."""
    result = describe(tmp_path, 'repeat { tick(); if skip() { continue } } while more(); for x in items() where valid(x) { work(x) }; after()')
    assert names(result) == ['tick', 'skip', 'more', 'items', 'valid', 'work', 'after']
    repeat, collection, foreach, after = result['methodFlow']['steps']
    assert repeat['controlKind'] == 'do' and repeat['condition'][0]['label'] == 'more()'
    assert repeat['body'][0]['label'] == 'tick()'
    assert collection['label'] == 'items()' and foreach['controlKind'] == 'foreach'
    assert foreach['body'][0]['label'] == 'valid()'
    filtered = foreach['body'][1]
    assert filtered['arms'][0]['steps'][0]['label'] == 'work()'
    assert filtered['arms'][1]['emptyReason'] == 'skip'
    assert result['events'][3]['conditional'] is False
    assert all(e['conditional'] for e in result['events'][4:6])
    assert not result['methodFlow']['unsupported']


def test_optional_receiver_call_evaluated_once_before_optional_arguments(tmp_path):
    """A single optional hop may follow an eager receiver call without expanding a propagation chain."""
    result = describe(tmp_path, 'receiver()?.go(arg()); after()')
    assert names(result) == ['receiver', 'arg', 'go', 'after']
    assert [event['conditional'] for event in result['events']] == [False, True, True, False]
    assert not result['methodFlow']['unsupported']


def test_labeled_jump_unknown_is_terminal_and_not_loop_fallthrough(tmp_path):
    """The UI treats unknown as no known successor; a named break cannot loop back implicitly."""
    result = describe(tmp_path, 'outer: while ready() { break outer; dead() }; after()')
    assert names(result) == ['ready', 'after']
    loop = next(s for s in result['methodFlow']['steps'] if s['kind'] == 'loop')
    assert len(loop['body']) == 1 and loop['body'][0]['kind'] == 'unknown'
    assert 'target routing' in loop['body'][0]['unsupported']


def test_computed_property_and_expression_pattern_are_explicit_boundaries(tmp_path):
    """Implicit accessor/matching semantics cannot hide missing calls in a purportedly complete tree."""
    result = describe(tmp_path, 'var x: Int { getter(); return 1 }; switch tag() { case pattern(): work(); default: break }; after()')
    assert names(result) == ['after']
    unsupported = '\n'.join(result['methodFlow']['unsupported'])
    assert 'accessor body is deferred' in unsupported and 'ordered pattern evaluation' in unsupported


def test_failable_initializer_keeps_nil_exit_and_success_path():
    """Failable init labels still select the exact declaration; no implicit cleanup is invented."""
    text = 'struct C { init?(_ x: Int) { guard x > 0 else { return nil }; ready() } }'
    result = analyze({'text': text}, {'label': 'init?()', 'line': 1}, max_events=10)
    assert result['sourceStatus'] == 'parsed' and names(result) == ['ready']
    guard = result['methodFlow']['steps'][-1]
    assert guard['arms'][1]['steps'][-1]['kind'] == 'return'
    assert guard['arms'][1]['steps'][-1]['label'] == 'return nil'
    assert result['events'][0]['conditional']
