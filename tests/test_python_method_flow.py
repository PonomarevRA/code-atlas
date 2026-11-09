"""Python tree acceptance through adapter, real SourceAnalysis and graph-bound description APIs."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.python import analyze
from atlas.member_graph import enrich_member_graph
from atlas.node_description import build_node_description

FIXTURE = Path(__file__).parent / 'fixtures/flow/python/controls.py'


def direct(text, name='run', line=None, max_events=160, **facts):
    """Invoke the adapter against source text without importing the investigated fixture."""
    node = {'id': 'raw-run', 'label': name + '()', **facts}
    if line is not None:
        node['line'] = line
    return analyze({'text': text, 'file': 'code.py'}, node, max_events=max_events)


def described(tmp_path, text, name='run', line=None, max_events=160):
    """Exercise the actual safe reader and shared adapter host, preserving every source byte."""
    path = tmp_path / 'code.py'
    path.write_text(text)
    node = {'id': 'raw-run', 'label': name + '()', 'kind': 'method', 'file': 'code.py'}
    if line is not None:
        node['line'] = line
    original = deepcopy(node)
    result = SourceAnalysis(tmp_path, 's', 5, 1024 * 1024, max_events).describe(node)
    assert node == original
    assert path.read_text() == text
    assert result['sourceHash'] == sha256(text.encode()).hexdigest()
    return result


def walk(steps):
    """Visit nested tree rows without treating alternatives as one execution trace."""
    for step in steps:
        yield step
        for arm in step.get('arms', []):
            yield from walk(arm['steps'])
        for field in ('condition', 'body', 'update'):
            yield from walk(step.get(field, []))


def names(result):
    """Expose event order for assertions independently of visual row order."""
    return [event['callee'] for event in result['events']]


def test_real_host_controls_order_boundaries_and_async(tmp_path):
    """Decorators, unreachable statements and deferred fixture module code remain unexecuted/excluded."""
    result = described(tmp_path, FIXTURE.read_text())
    assert result['sourceStatus'] == 'parsed'
    assert names(result) == ['first', 'inner', 'second', 'wrap', 'check', 'allowed', 'send',
                             'reject', 'load', 'skip', 'consume', 'pending', 'drain', 'finish']
    assert result['documentation']['summary'] == "Only this callable's documentation."
    assert result['returnType'] == "'Result'"
    assert result['parameters'] == [{'name': 'items', 'type': "'Sequence'"}, {'name': 'enabled', 'type': ''}]
    events = {event['callee']: event for event in result['events']}
    assert not events['check']['conditional'] and events['allowed']['conditional']
    assert events['send']['awaited'] and not events['send']['parallel']
    assert not events['load']['conditional'] and events['consume']['conditional']
    steps = result['methodFlow']['steps']
    assert not result['methodFlow']['unsupported']
    assert [s['kind'] for s in steps][-4:] == ['loop', 'loop', 'call', 'return']
    assert [s['label'] for s in steps[-3]['condition']] == ['pending()']
    assert {t['kind'] for t in result['controlFlow']['terminals']} == {'continue', 'break', 'return'}
    assert result['parameterFacts']['status'] == result['attributeFacts']['status'] == 'unavailable'
    assert 'C#' not in result['parameterFacts']['warning']
    call_rows = [s for s in walk(steps) if s['kind'] == 'call']
    assert len(call_rows) == len(result['events'])
    assert [s['site'] for s in call_rows] == [dict((k, e[k]) for k in ('line','column','endLine','endColumn')) for e in result['events']]
    valid_arms = {a['id'] for b in result['controlFlow']['branches'] for a in b['arms']}
    assert all(set(e['branchIds']) <= valid_arms for e in result['events'])


@pytest.mark.parametrize('body,expected', [
    ('return get().method(a(), kw=b())', ['get', 'a', 'b', 'method']),
    ('return call(k=keyword(), *starred())', ['starred', 'keyword', 'call']),
    ('return {key1(): val1(), key2(): val2()}', ['key1','val1','key2','val2']),
    ('target()[index()] = value()', ['value', 'target', 'index']),
    ('target()[index()] += value()', ['target', 'index', 'value']),
    ('return seq()[lower():upper():stride()]', ['seq','lower','upper','stride']),
    ('return f"{left()}:{right()}"', ['left','right']),
    ('raise Error(message()) from cause()', ['message','Error','cause']),
])
def test_python_evaluation_order(tmp_path, body, expected):
    """AST field order alone is insufficient for calls, dictionaries and assignment targets."""
    result = described(tmp_path, 'def run():\n    ' + body + '\n')
    assert names(result) == expected
    assert not result['methodFlow']['unsupported']


def test_boolean_ternary_and_chained_comparisons(tmp_path):
    """Every later operand stays under all preceding short-circuit guards; no call is repeated."""
    result = described(tmp_path, 'def run():\n    x = a() and b() and c()\n    y = d() or e()\n    z = yes() if test() else no()\n    return low() < mid() < high() < last()\n')
    assert names(result) == ['a','b','c','d','e','test','yes','no','low','mid','high','last']
    events = {e['callee']:e for e in result['events']}
    assert len(events['b']['branchIds']) == 1 and len(events['c']['branchIds']) == 2
    assert events['yes']['branchIds'] != events['no']['branchIds']
    assert not events['mid']['conditional'] and events['high']['conditional']
    assert len(events['last']['branchIds']) == 2
    branches = [s for s in walk(result['methodFlow']['steps']) if s['kind'] == 'branch']
    assert {b['controlKind'] for b in branches} == {'and','or','conditional','comparison'}
    assert any(a.get('emptyReason') == 'skip' for b in branches for a in b['arms'])


def test_eager_comprehensions_nest_iterables_filters_and_dict_pairs(tmp_path):
    """Only the outer iterable is unconditional; inner iterables follow the preceding filters."""
    result = described(tmp_path, FIXTURE.read_text(), name='eager')
    assert names(result) == ['outer','keep','inner','accept','key','value']
    events = {e['callee']: e for e in result['events']}
    assert not events['outer']['conditional']
    assert len(events['inner']['branchIds']) == 2
    assert len(events['key']['branchIds']) == 4
    assert not result['methodFlow']['unsupported']
    assert sum(s['kind'] == 'loop' for s in walk(result['methodFlow']['steps'])) == 2


def test_deferred_functions_lambda_generator_creation_and_defaults(tmp_path):
    """Definition defaults and generator outer iterable execute at creation; deferred bodies do not."""
    result = described(tmp_path, FIXTURE.read_text(), name='deferred')
    assert names(result) == ['default','seed','source','use']
    assert sum(bool(s.get('deferred')) for s in walk(result['methodFlow']['steps'])) == 3
    assert any('deferred' in reason for reason in result['methodFlow']['unsupported'])


@pytest.mark.parametrize('name,reason', [('generator','suspension'), ('cleanup','Try')])
def test_generator_and_finally_are_opaque_not_linear(tmp_path, name, reason):
    """No partial trace invents ordering across suspension or exception/cleanup paths."""
    result = described(tmp_path, FIXTURE.read_text(), name=name)
    assert names(result) == []
    assert len(result['methodFlow']['steps']) == 1
    assert result['methodFlow']['steps'][0]['kind'] == 'unknown'
    assert any(reason in entry for entry in result['methodFlow']['unsupported'])


@pytest.mark.parametrize('body,reason', [
    ('for x in items():\n        work()\n    else:\n        exhausted()\n    after()', 'Loop else'),
    ('while check():\n        break\n    else:\n        exhausted()', 'Loop else'),
    ('with open_file():\n        work()\n    after()', 'With'),
    ('async for item in items():\n        work()', 'Async-for'),
    ('return [work(x) async for x in items()]', 'Async comprehension'),
    ('assert check(), message()', 'Assert'),
    ('match check():\n        case 1 if guard():\n            work()', 'Match'),
    ('class Inner:\n        hidden()\n    after()', 'Nested class'),
    ('@decorator(factory())\n    def inner():\n        hidden()\n    after()', 'Nested class/decorated'),
    ('import dangerous_module\n    after()', 'Import'),
])
def test_unsupported_constructs_have_visible_boundaries(tmp_path, body, reason):
    """Unsupported paths never masquerade as extracted unconditional call chains."""
    result = described(tmp_path, 'async def run():\n    ' + body + '\n')
    assert names(result) == []
    assert any(reason in entry for entry in result['methodFlow']['unsupported'])
    assert any(s['kind'] == 'unknown' for s in walk(result['methodFlow']['steps']))


def test_exact_declaration_identity_and_own_documentation(tmp_path):
    """Class ownership and exact start defeat same-name siblings and stale source ranges."""
    text = 'class A:\n    """Type docs."""\n    def run(self):\n        left()\nclass B:\n    def run(self):\n        right()\n'
    assert direct(text)['sourceStatus'] == 'unresolved'
    assert names(described(tmp_path,text,line=6)) == ['right']
    assert direct(text,line=4)['sourceStatus'] == 'unresolved'
    result = direct(text, callableFacts={'declaringType':'A'})
    assert names(result) == ['left']
    assert not result['documentation']['summary']
    assert result['typeContext']['documentation']['summary'] == 'Type docs.'
    assert direct(text, line=3, callableFacts={'declaringType':'B'})['sourceStatus'] == 'unresolved'


@pytest.mark.parametrize('text', ['def run(:\n broken', 'def neighbor():\n    hidden()\n'])
def test_invalid_or_missing_declaration_has_no_neighbor_body(tmp_path, text):
    """Parse failures and absent declarations return no invented events/tree."""
    result = described(tmp_path, text)
    assert result['sourceStatus'] in {'unavailable','unresolved'}
    assert result['events'] == [] and result['methodFlow']['steps'] == []
    assert result['unresolved']


def test_real_graph_description_binds_raw_ids_and_preserves_inputs(tmp_path):
    """Actual member capture and node-description binding retain graph identity, bytes and hashes."""
    text = 'def leaf(x):\n    return x\n\ndef run():\n    return leaf(inner())\n'
    (tmp_path / 'code.py').write_text(text)
    graph = enrich_member_graph({'nodes':[{'id':'raw-file', 'file':'code.py', 'label':'code.py', 'kind':'file', 'line':1}], 'edges':[]}, tmp_path)
    run = next(n for n in graph['nodes'] if n.get('memberFacts',{}).get('name') == 'run')
    leaf = next(n for n in graph['nodes'] if n.get('memberFacts',{}).get('name') == 'leaf')
    graph['edges'].append({'source':run['id'], 'target':leaf['id'], 'relation':'calls', 'sourceLine':5, 'confidence':'EXTRACTED'})
    original = deepcopy(graph)
    result = build_node_description(tmp_path, graph, run['id'], 's')
    assert graph == original and (tmp_path / 'code.py').read_text() == text
    assert names(result) == ['inner','leaf']
    assert result['events'][1]['targetNodeIds'] == [leaf['id']]
    assert result['nodeId'] == run['id'] and result['sourceStatus'] == 'parsed'
    assert result['sourceHash'] == sha256(text.encode()).hexdigest()
    assert result['methodFlow']['status'] == 'parsed'


def test_utf8_sites_and_docstring_budget(tmp_path):
    """Sites match original UTF-8 bytes even when a previous expression contains non-ASCII text."""
    text = 'def run():\n    """' + 'я' * 8100 + '"""\n    café("я"); other()\n'
    result = described(tmp_path,text)
    assert result['documentation']['truncated'] and len(result['documentation']['summary']) == 8000
    lines = text.encode().splitlines()
    for event in result['events']:
        assert lines[event['line']-1][event['column']-1:event['endColumn']-1].decode() == event['text']


def test_budget_events_steps_depth_and_ast_are_explicit(tmp_path):
    """Resource limits bound both representations, with no unlinked call rows beyond the cap."""
    result = described(tmp_path, 'def run():\n    outer(first(), second())\n', max_events=1)
    assert names(result) == ['first']
    assert result['eventsTruncated'] and result['methodFlow']['truncated']
    result = direct('def run():\n' + '    call()\n' * 300, max_events=1000)
    assert len(list(walk(result['methodFlow']['steps']))) <= 180
    assert result['methodFlow']['truncated']
    result = direct('def run():\n    ' + 'f(' * 40 + '0' + ')' * 40 + '\n')
    assert result['methodFlow']['truncated']
    result = direct('def run():\n    x = [' + ','.join('1' for _ in range(21000)) + ']\n')
    assert result['sourceStatus'] == 'unavailable' and result['methodFlow']['truncated']
    assert result['events'] == []


def test_empty_and_literal_arms_and_terminal_no_fallthrough(tmp_path):
    """Value-only branches remain explicit, and both terminal arms exclude the following statement."""
    result = described(tmp_path, 'def run(x):\n    y = 1 if x else 2\n    if x:\n        return y\n    else:\n        raise Error()\n    unreachable()\n')
    assert names(result) == ['Error']
    branch = result['methodFlow']['steps'][0]
    assert [[s['expression'] for s in a['steps']] for a in branch['arms']] == [['1'], ['2']]


def test_host_safety_and_budgets(tmp_path):
    """The adapter inherits the safe reader and file/byte limits rather than opening source itself."""
    (tmp_path / 'code.py').write_text('def run():\n    work()\n')
    node = {'id':'raw','file':'code.py','label':'run()','line':1}
    analysis = SourceAnalysis(tmp_path,'s',0,1024,10)
    assert analysis.describe(node)['sourceStatus'] == 'unavailable' and analysis.truncation['files']
    analysis = SourceAnalysis(tmp_path,'s',1,1,10)
    assert analysis.describe(node)['sourceStatus'] == 'unavailable' and analysis.truncation['bytes']
    node['file'] = '../secret.py'
    assert SourceAnalysis(tmp_path,'s',1,1024,10).describe(node)['events'] == []


def test_signature_preserves_defaults_separators_annotations_and_comments(tmp_path):
    """Signature text stays exact even with colons in defaults and comments preceding the body."""
    header = 'async def run(a: int = 1, /, *args: str, flag="x:y", **kw) -> Result'
    result = described(tmp_path, header + ': # note: ignored\n    # more: ignored\n    return done()\n')
    assert result['signature'] == header
    assert result['returnType'] == 'Result'
    assert [p['name'] for p in result['parameters']] == ['a','args','flag','kw']


def test_class_and_function_identity_never_cross(tmp_path):
    """Declaration kind is part of identity, including when a class and callable share their name."""
    text = 'class Same:\n    pass\ndef Same():\n    method()\n'
    assert names(direct(text, name='Same')) == ['method']
    result = direct(text, name='Same', kind='class')
    assert result['kind'] == 'class' and result['methodFlow']['status'] == 'declaration-only'
    assert not result['events']


def test_immediate_lambda_and_annotated_nested_declarations_report_omissions(tmp_path):
    """Known unexpanded invocation bodies and annotation effects cannot claim complete extraction."""
    result = described(tmp_path,'def run():\n    return (lambda: hidden())()\n')
    assert names(result) == ['']
    assert any('Immediately invoked lambda' in u for u in result['methodFlow']['unsupported'])
    result = described(tmp_path,'def run():\n    def nested(*args: annotation()):\n        hidden()\n    after()\n')
    assert not result['events']
    assert result['methodFlow']['steps'][0]['kind'] == 'unknown'


@pytest.mark.parametrize('syntax', ['[value(x) for x in items()]', '{value(x) for x in items()}'])
def test_eager_list_and_set_comprehensions(tmp_path, syntax):
    """List/set comprehensions share eager guarded iteration rather than generator deferral."""
    result = described(tmp_path,'def run():\n    return ' + syntax + '\n')
    assert names(result) == ['items','value']
    assert not result['events'][0]['conditional'] and result['events'][1]['conditional']
    assert not result['methodFlow']['unsupported']


def test_async_generator_is_opaque_and_closure_yield_is_local(tmp_path):
    """A nested yield must not turn its enclosing ordinary method into a generator."""
    result = described(tmp_path,'async def run():\n    yield await item()\n')
    assert result['methodFlow']['status'] == 'declaration-only' and not result['events']
    result = described(tmp_path,'def run():\n    def closure():\n        yield item()\n    return done()\n')
    assert names(result) == ['done'] and result['methodFlow']['status'] == 'parsed'


def test_nested_same_names_require_exact_line_and_decorated_start_matches(tmp_path):
    """Deferred nested declarations can be selected independently without leaking their siblings."""
    text = 'def outer():\n    def run():\n        inner()\n@decorate()\ndef run():\n    outer_call()\n'
    assert direct(text)['sourceStatus'] == 'unresolved'
    assert names(described(tmp_path,text,line=2)) == ['inner']
    assert names(described(tmp_path,text,line=4)) == ['outer_call']
    assert direct(text,line=3)['sourceStatus'] == 'unresolved'


def test_dead_arm_tails_do_not_hide_definite_exits(tmp_path):
    """Source after an unconditional arm exit cannot resurrect a common fallthrough path."""
    text = 'def run(x):\n    if x:\n        return a()\n        dead()\n    else:\n        return b()\n        dead()\n    after()\n'
    assert names(described(tmp_path,text)) == ['a','b']


def test_comprehension_target_evaluation_is_inside_iteration(tmp_path):
    """Assignment to an attribute/subscript target can itself contain explicit calls."""
    result = described(tmp_path,'def run():\n    return [value() for store()[index()] in items() if check()]\n')
    assert names(result) == ['items','store','index','check','value']
    assert all(e['conditional'] for e in result['events'][1:])


def test_many_comprehension_guards_obey_depth_limit(tmp_path):
    """Iteratively built guards must respect the same nesting limit as recursive AST paths."""
    result = described(tmp_path, 'def run():\n    return [work(x) for x in items()' + ' if check()' * 80 + ']\n')
    assert result['methodFlow']['truncated']
    assert len(result['events']) <= 24


def test_await_of_conditional_marks_selected_direct_calls(tmp_path):
    """Await applies to the selected result without marking nested argument calls as awaited."""
    result = described(tmp_path,'async def run(x):\n    return await (a(inner()) if x else b())\n')
    assert names(result) == ['inner','a','b']
    assert [e['awaited'] for e in result['events']] == [False, True, True]


def test_budget_keeps_known_skip_distinct_from_unextracted_arm(tmp_path):
    """A limit in the evaluated arm does not make the real skipped alternative unknown."""
    result = described(tmp_path,'def run():\n    return a() and b()\n',max_events=1)
    branch = result['methodFlow']['steps'][1]
    assert branch['arms'][0]['emptyReason'] == 'not-extracted'
    assert branch['arms'][1]['emptyReason'] == 'skip'
    assert result['eventsTruncated'] and result['unresolved']


def test_yield_in_nested_default_suspends_outer_generator(tmp_path):
    """Skipping deferred bodies must still inspect expressions evaluated during declaration creation."""
    result = described(tmp_path,'def run():\n    def nested(x=(yield item())):\n        hidden()\n    after()\n')
    assert result['methodFlow']['status'] == 'declaration-only'
    assert not result['events']


def test_await_of_boolean_result_exposes_binding_limit(tmp_path):
    """The tree retains lexical alternatives while refusing to guess which operand result is awaited."""
    result = described(tmp_path,'async def run():\n    return await (a() and b())\n')
    assert names(result) == ['a','b']
    assert any('per-call await binding' in u for u in result['methodFlow']['unsupported'])


def test_long_labels_show_truncation_and_keep_exact_sites(tmp_path):
    """A bounded display label cannot silently replace the actual source range."""
    text = 'def run():\n    return consume("' + 'x' * 600 + '")\n'
    result = described(tmp_path,text)
    call = result['methodFlow']['steps'][0]
    assert len(call['label']) == 400 and call['labelTruncated']
    assert result['events'][0]['sourceExpressionTruncated']
    assert result['events'][0]['endColumn'] > 600


def test_exact_representative_tree(tmp_path):
    """Compare the complete structural tree, including skipped alternatives and loop jump positions."""
    text = '''def run():
    x = a() if choose() else b()
    while check():
        if skip():
            continue
        work()
        break
    return x
'''
    result = described(tmp_path,text)

    def shape(steps):
        """Project away provenance while retaining all control/evaluation children in the assertion."""
        rows = []
        for step in steps:
            row = [step['kind'],step['label']]
            if step['kind'] == 'branch':
                row.append([(arm['label'],shape(arm['steps']),arm.get('emptyReason')) for arm in step['arms']])
            if step['kind'] == 'loop':
                row.extend([shape(step['condition']),shape(step['body']),shape(step['update'])])
            rows.append(row)
        return rows

    assert shape(result['methodFlow']['steps']) == [
        ['call','choose()'],
        ['branch','a() if choose() else b()', [
            ('true',[['call','a()']],None), ('false',[['call','b()']],None)]],
        ['statement','x = a() if choose() else b()'],
        ['loop','check()', [['call','check()']], [
            ['call','skip()'],
            ['branch','skip()', [('true',[['continue','continue']],None), ('false',[],'skip')]],
            ['call','work()'], ['break','break']], []],
        ['return','return x']]
