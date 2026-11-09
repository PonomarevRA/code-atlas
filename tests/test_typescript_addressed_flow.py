"""Addressed TypeScript flow keeps original scope, byte coordinates and bounded request reuse."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from weakref import ref

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.parsed_source import ParseBudget, parsed_tree
from atlas.flow_languages.typescript import analyze
from atlas.flow_languages.typescript_support import source as selection
from atlas.member_graph import enrich_member_graph
from atlas.node_description import build_node_description


def large_source(declaration):
    """Surround one selected declaration with an unrelated body above the old whole-file cap."""
    return 'function enormousNeighbour(){' + 'unused();' * 6000 + '}\n' + declaration


def method_node(*, filename='Code.ts', name='run', line=2, **fields):
    """Build a legacy raw identity whose line still constrains the original source tree."""
    return dict(id='selected', label=name + '()', file=filename, kind='method', line=line, **fields)


def call_names(result):
    """Read lexical call facts without treating them as runtime execution evidence."""
    return [event['callee'] for event in result['events']]


@pytest.mark.parametrize('suffix,declaration,expected', [
    ('.ts', 'export function run(x: number){ return chosen(x); }', ['chosen']),
    ('.tsx', 'export const run = (x: number) => <Panel value={chosen(x)}/>;', ['chosen']),
])
def test_real_capture_and_public_description_of_small_method_in_large_file(tmp_path, monkeypatch, suffix, declaration, expected):
    """Member capture and both public host paths select a small declaration after a large sibling."""
    filename = 'Code' + suffix
    content = large_source(declaration)
    (tmp_path / filename).write_text(content)
    graph = enrich_member_graph({'nodes': [dict(id='file', label=filename, file=filename, kind='file', line=1)], 'edges': []}, tmp_path)
    node = next(n for n in graph['nodes'] if n.get('memberFacts', {}).get('name') == 'run')
    before = deepcopy(graph)
    # Fifty selection visits suffice because the unrelated body is not traversed.
    monkeypatch.setattr(selection, 'MAX_SELECTION_NODES', 50)
    analysis = SourceAnalysis(tmp_path, 'large', 3, 1024 * 1024, 160)
    direct = analysis.describe(node)
    public = build_node_description(tmp_path, graph, node['id'], 'large')
    assert direct['sourceStatus'] == public['sourceStatus'] == 'parsed'
    assert direct['methodFlow'] == public['methodFlow']
    assert call_names(public) == expected
    assert public['sourceHash'] == sha256(content.encode()).hexdigest()
    assert public['methodFlow']['limits']['maxAstNodes'] == 20_000
    assert public['methodFlow']['limits']['maxDepth'] == 24
    assert public['methodFlow']['limits']['maxSteps'] == 180
    assert public['readabilityRecommendations']['status'] == 'unavailable'
    assert public['readabilityRecommendations']['reason'] == 'unsupported-language'
    assert public['readabilityRecommendations']['items'] == []
    assert graph == before


def test_exact_byte_column_skips_large_sibling_on_same_line(monkeypatch):
    """A byte column can address one function even when an earlier huge body shares its line."""
    prefix = 'function unrelated(){' + 'unused();' * 6000 + '} '
    text = prefix + 'function run(){ chosen(); }'
    node = method_node(line=1, column=len(prefix.encode()) + 1)
    monkeypatch.setattr(selection, 'MAX_SELECTION_NODES', 50)
    result = analyze({'text': text, 'file': 'Code.ts'}, node, max_events=20)
    assert result['sourceStatus'] == 'parsed' and call_names(result) == ['chosen']
    node.pop('column')
    node['startByte'] = len(prefix.encode())
    assert call_names(analyze({'text': text, 'file': 'Code.ts'}, node, max_events=20)) == ['chosen']


def test_same_line_legacy_selection_budget_fails_closed():
    """An incomplete candidate scan cannot establish uniqueness for a line-only identity."""
    text = 'function unrelated(){' + 'unused();' * 6000 + '} function run(){ chosen(); }'
    result = analyze({'text': text, 'file': 'Code.ts'}, method_node(line=1), max_events=20)
    assert result['sourceStatus'] == 'unresolved'
    assert result['methodFlow']['truncated'] and not result['events']
    assert 'selection' in result['unresolved'][0]


def test_owner_parameters_signature_and_exact_endpoints_are_always_checked():
    """Even one candidate cannot bypass supplied stale signature or byte-span evidence."""
    text = 'class A { run(x: string){ chosen(); } }'
    start = len('class A { '.encode())
    end = len(text.encode()) - 2
    good = method_node(line=1, column=start + 1, startByte=start, endByte=end,
                       callableFacts={'declaringType': 'A', 'parameterList': '(x: string)', 'signature': 'run(x: string)'})
    assert call_names(analyze({'text': text, 'file': 'Code.ts'}, good, max_events=20)) == ['chosen']
    for override in (
        {'startByte': start + 1}, {'endByte': end - 1}, {'endLine': 2}, {'endColumn': end},
        {'callableFacts': {'declaringType': 'B'}},
        {'callableFacts': {'parameterList': '(x: number)'}},
        {'callableFacts': {'signature': 'run(x: number)'}},
    ):
        result = analyze({'text': text, 'file': 'Code.ts'}, {**good, **override}, max_events=20)
        assert result['sourceStatus'] == 'unresolved' and not result['events'], override
    moved = analyze({'text': 'function run(){\n chosen();\n}', 'file': 'Code.ts'}, method_node(), max_events=20)
    assert moved['sourceStatus'] == 'unresolved' and not moved['events']


@pytest.mark.parametrize('declaration,name,line,owner', [
    ('class A {\n @tracked\n run(x: number){ return chosen(x); }\n}', 'run', 4, 'A'),
    ('class A {\n run =\n ((x: number) => chosen(x));\n}', 'run', 3, 'A'),
    ('export const run =\n (x: number) => chosen(x);', 'run', 2, 'Code.ts'),
    ('class A { run(){ wrong(); } }\nclass B { run(){ chosen(); } }', 'run', 3, 'B'),
])
def test_decorated_fields_exports_and_owners_retain_original_parent_context(declaration, name, line, owner):
    """Selection of wrapped multi-line arrows follows their binding without fragment parsing."""
    result = analyze({'text': large_source(declaration), 'file': 'Code.ts'},
                     method_node(name=name, line=line, callableFacts={'declaringType': owner, 'parameterList': '(x: number)'} if 'x: number' in declaration else {'declaringType': owner}),
                     max_events=20)
    assert result['sourceStatus'] == 'parsed', result['unresolved']
    assert call_names(result) == ['chosen']
    if owner != 'Code.ts':
        assert result['typeContext']['name'] == owner


def test_utf8_bytes_and_stale_file_keep_source_evidence(tmp_path):
    """UTF-8 call columns stay absolute; changed declarations cannot reuse old parameter evidence."""
    text = large_source('function run(x: string){ const s = "ёж"; chosen(x); }')
    path = tmp_path / 'Code.ts'
    path.write_text(text)
    node = method_node(callableFacts={'parameterList': '(x: string)', 'sourceHash': sha256(text.encode()).hexdigest()})
    first = SourceAnalysis(tmp_path, 's', 2, 1024 * 1024, 20).describe(node)
    assert first['events'][0]['column'] == len(text.splitlines()[1].split('chosen')[0].encode()) + 1
    changed = text.replace('x: string', 'x: number')
    path.write_text(changed)
    second = SourceAnalysis(tmp_path, 's', 2, 1024 * 1024, 20).describe(node)
    assert second['sourceStatus'] == 'unresolved' and second['events'] == []
    assert second['sourceHash'] == sha256(changed.encode()).hexdigest() != first['sourceHash']


def test_large_selected_body_remains_unavailable_and_deep_callbacks_remain_bounded():
    """Moving the selection cap does not increase selected-body, depth or event limits."""
    huge = analyze({'text': large_source('function run(){\n' + 'chosen();\n' * 6000 + '}'), 'file': 'Code.ts'}, method_node(), max_events=20)
    assert huge['methodFlow']['status'] == 'unavailable' and huge['methodFlow']['truncated']
    assert 'body budget' in huge['unresolved'][0] and not huge['events']
    deep = analyze({'text': large_source('function run(){' + 'wrap(' * 40 + '() => deferred()' + ')' * 40 + ';}'), 'file': 'Code.ts'}, method_node(), max_events=20)
    assert deep['methodFlow']['truncated']
    assert 'deferred' not in call_names(deep)
    assert len(deep['events']) <= 20


def test_ast_limit_is_visible_in_host_request_truncation(tmp_path):
    """Aggregate host truncation records the selected-body limit without marking it complete."""
    (tmp_path / 'Code.ts').write_text('function run(){\n' + 'chosen();\n' * 6000 + '}')
    analysis = SourceAnalysis(tmp_path, 's', 2, 1024 * 1024, 20)
    result = analysis.describe(method_node(line=1))
    assert analysis.truncation['ast'] and result['declarationTruncated']
    assert result['methodFlow']['reason'] == 'body-ast-budget'


def counting_parser(monkeypatch):
    """Count real parse calls while preserving the installed TypeScript and TSX grammars."""
    import tree_sitter
    real, calls = tree_sitter.Parser, []

    class Parser:
        """Wrap a fresh parser per actual parse; no mutable parser is shared across threads."""
        def __init__(self, *args, **kwargs):
            self.inner = real(*args, **kwargs)

        def parse(self, content):
            calls.append(len(content))
            return self.inner.parse(content)

    monkeypatch.setattr(tree_sitter, 'Parser', Parser)
    return calls


def test_repeated_and_concurrent_host_descriptions_parse_once(tmp_path, monkeypatch):
    """One host request shares a bounded tree and lock while every result remains independent."""
    (tmp_path / 'Code.ts').write_text(large_source('function run(){ chosen(); }\nfunction other(){ second(); }'))
    calls = counting_parser(monkeypatch)
    analysis = SourceAnalysis(tmp_path, 's', 2, 1024 * 1024, 20)
    nodes = [method_node(), method_node(name='other', line=3)] * 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(analysis.describe, nodes))
    assert len(calls) == 1
    assert [call_names(result) for result in results] == [['chosen'], ['second']] * 4
    results[0]['events'].clear()
    assert call_names(analysis.describe(nodes[0])) == ['chosen']
    budget = analysis._flow_parse_budget
    assert 20_000 < budget.nodes <= 100_000
    assert budget.bytes <= 1024 * 1024


def test_destroyed_hosts_release_retained_native_tree_owners_without_gc(tmp_path):
    """A sequence of short-lived hosts must not leave budget/cache cycles retaining native trees."""
    (tmp_path / 'Code.ts').write_text(large_source('function run(){ chosen(); }'))
    references = []
    for _ in range(20):
        analysis = SourceAnalysis(tmp_path, 's', 2, 1024 * 1024, 20)
        assert call_names(analysis.describe(method_node())) == ['chosen']
        assert analysis._flow_parse_budget.nodes > 20_000
        references.append(ref(analysis._flow_parse_budget))
        del analysis
        assert all(reference() is None for reference in references)


def test_changed_text_preserves_weak_budget_ownership():
    """Invalidation cannot silently turn a weak budget backref into a native-tree retention cycle."""
    import ast
    budget = ParseBudget()
    source = {'text': 'before', '_flow_parse_cache': {'_budget': budget}}
    parsed_tree(source, 'python', lambda data: ast.parse('pass'))
    source['text'] = 'after'
    parsed_tree(source, 'python', lambda data: ast.parse('pass'))
    reference = ref(budget)
    del source, budget
    assert reference() is None


def test_host_lru_eviction_and_oversized_fresh_fallback_are_explicit(tmp_path, monkeypatch):
    """Evicted or unretained syntax reparses accurately without growing retained counters."""
    import atlas.flow_languages.host as host
    monkeypatch.setattr(host, 'ParseBudget', lambda: ParseBudget(max_nodes=50_000))
    for name in ('A.ts', 'B.ts'):
        (tmp_path / name).write_text(large_source('function run(){ chosen(); }'))
    calls = counting_parser(monkeypatch)
    analysis = SourceAnalysis(tmp_path, 's', 3, 1024 * 1024, 20)
    for name in ('A.ts', 'B.ts', 'A.ts'):
        assert call_names(analysis.describe(method_node(filename=name))) == ['chosen']
        assert analysis._flow_parse_budget.nodes <= 50_000
    assert len(calls) == 3
    assert analysis.files['B.ts']['_flow_parse_cache']['trees'] == {}
    huge = 'function unrelated(){' + 'unused();' * 16_000 + '}\nfunction run(){ chosen(); }'
    (tmp_path / 'Large.ts').write_text(huge)
    node = method_node(filename='Large.ts')
    for _ in range(2):
        assert call_names(analysis.describe(node)) == ['chosen']
    assert len(calls) == 5
    cache = analysis.files['Large.ts']['_flow_parse_cache']
    assert cache['trees'] == {} and 'typescript' in cache['unretained']


def test_cache_byte_cap_and_changed_text_release_old_accounting():
    """The aggregate encoded-source cap works independently of native syntax node count."""
    import ast
    budget = ParseBudget(max_nodes=100, max_bytes=8)
    first = {'text': 'aaaaaa', '_flow_parse_cache': {'_budget': budget}}
    second = {'text': 'bbbbbb', '_flow_parse_cache': {'_budget': budget}}
    parse = lambda content: ast.parse('pass')
    parsed_tree(first, 'python', parse)
    parsed_tree(second, 'python', parse)
    assert budget.bytes == 6 and budget.nodes == 2
    assert first['_flow_parse_cache']['trees'] == {}
    second['text'] = 'c'
    parsed_tree(second, 'python', parse)
    assert budget.bytes == 1 and budget.nodes == 2 and len(budget.entries) == 1


def test_missing_grammar_is_explicit_and_legacy_missing_span_is_bounded(monkeypatch):
    """An absent optional grammar and a missing source address cannot become successful checks."""
    import sys
    no_address = dict(id='selected', label='run()')
    result = analyze({'text': large_source('function run(){ chosen(); }'), 'file': 'Code.ts'}, no_address, max_events=20)
    assert result['sourceStatus'] == 'unresolved' and result['methodFlow']['truncated']
    monkeypatch.setitem(sys.modules, 'tree_sitter_typescript', None)
    missing = analyze({'text': 'function run(){ chosen(); }', 'file': 'Code.ts'}, method_node(line=1), max_events=20)
    assert missing['sourceStatus'] == 'unavailable'
    assert missing['readabilityRecommendations']['status'] == 'unavailable'
    assert missing['readabilityRecommendations']['reason'] == 'unsupported-language'


def test_parser_failure_survives_public_host_and_does_not_poison_cache(tmp_path, monkeypatch):
    """A provider failure is an explicit analysis error, and a later parse can retry normally."""
    import tree_sitter
    (tmp_path / 'Code.ts').write_text('function run(){ chosen(); }')
    node = method_node(line=1)
    real = tree_sitter.Parser

    class FailedParser:
        """Simulate a parser failure without changing the analyzed project or grammar files."""
        def __init__(self, *args, **kwargs):
            pass

        def parse(self, content):
            raise ValueError('failure details are not copied into the response')

    monkeypatch.setattr(tree_sitter, 'Parser', FailedParser)
    analysis = SourceAnalysis(tmp_path, 's', 2, 1024 * 1024, 20)
    result = analysis.describe(node)
    assert result['sourceStatus'] == 'unavailable' and not result['events']
    assert result['methodFlow']['status'] == 'error'
    assert result['methodFlow']['reason'] == 'parser-error'
    assert analysis.files['Code.ts']['_flow_parse_cache']['trees'] == {}
    monkeypatch.setattr(tree_sitter, 'Parser', real)
    assert call_names(analysis.describe(node)) == ['chosen']
