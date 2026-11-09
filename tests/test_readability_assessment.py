"""Real grammar examples validate independent R scores, token formulae and conservative evidence."""

from copy import deepcopy
from math import log2
import pytest

from atlas.code_health import calculate_health, compact_health
from atlas.code_health_comparison import compare_readability, summarize
from atlas.code_health_source import parse_health_source
from atlas.readability_assessment import assess_readability


def measured(source, filename='sample.py'):
    """Parse source without executing it, including unresolved external calls and deferred declarations."""
    nodes = parse_health_source(filename, source.encode())['methods']
    for node in nodes:
        node['health'] = calculate_health(node, None)
    return nodes


@pytest.mark.parametrize('filename,source', [
    ('a.py', 'def pick(a,b,c,d,e):\n return a and b or c and d or e\n'),
    ('a.cs', 'class C { bool Pick(bool a,bool b,bool c,bool d,bool e){return a && b || c && d || e;} }'),
    ('a.js', 'function pick(a,b,c,d,e){return a && b || c && d || e;}'),
    ('a.ts', 'function pick(a:boolean,b:boolean,c:boolean,d:boolean,e:boolean){return a && b || c && d || e;}'),
    ('a.java', 'class C { boolean pick(boolean a,boolean b,boolean c,boolean d,boolean e){return a && b || c && d || e;} }'),
    ('a.go', 'package p\nfunc pick(a,b,c,d,e bool) bool {return a && b || c && d || e}'),
    ('a.rs', 'fn pick(a:bool,b:bool,c:bool,d:bool,e:bool)->bool {a && b || c && d || e}'),
])
def test_dense_conditions_lower_r_without_changing_h_policy(filename, source):
    node = measured(source, filename)[0]
    profile = node['health']['readability']
    assert node['health']['readabilityScore'] == profile['score'] == 88
    assert sum(item['appliedPenalty'] for item in profile['penalties']) == 12
    assert profile['verdict'] == 'REVIEW' and profile['humanReview'] == 'not-performed'
    assert node['health']['components']['flow'] == 100
    assert profile['calibration'] == 'uncalibrated-project-heuristic'


def test_halstead_base_counts_and_formulas_use_actual_tokens():
    node = measured('def add(x):\n return x + 1\n')[0]
    tokens = node['complexity']['halstead']
    assert (tokens['distinctOperators'], tokens['totalOperators']) == (2, 2)
    assert (tokens['distinctOperands'], tokens['totalOperands']) == (2, 2)
    assert tokens['volume'] == 4 * log2(4) == 8
    assert tokens['difficulty'] == 1 and tokens['effort'] == 8
    repeated = measured('def add(x):\n return x + x\n')[0]['complexity']['halstead']
    assert repeated['distinctOperands'] == 1 and repeated['totalOperands'] == 2
    assert repeated['difficulty'] == 2


def test_strings_are_atomic_and_comments_do_not_change_token_counts():
    source = 'def read():\n return "if a and b or c"\n'
    first = measured(source)[0]['complexity']['halstead']
    second = measured(source.replace(' return', ' # arbitrary /* && */ comment\n return'))[0]['complexity']['halstead']
    assert first == second
    assert first['totalOperands'] == 1 and first['totalOperators'] == 1
    assert first['effort'] != 0
    template = measured('function read(x){return `name ${x ? 1 : 2}`;}', 'a.js')[0]['complexity']['halstead']
    assert template['status'] == 'partial' and template['volume'] is None


def test_complete_r_is_useful_with_unknown_dependencies_but_never_approves_semantics():
    node = measured('def run(value):\n return provider.call(value)\n')[0]
    assert node['health']['score'] is None
    profile = node['health']['readability']
    assert profile['score'] == 100
    assert 'naming' in profile['unverifiedDimensions'] and 'correctness' in profile['unverifiedDimensions']
    assert profile['verdict'] != 'PASS'
    compact = compact_health(node)['health']
    assert compact['readabilityScore'] == 100 and compact['readability']['version'] == profile['version']
    assert compact['readability']['humanReview'] == 'not-performed'


def test_nested_logic_and_guards_have_different_r_without_penalizing_negations():
    nested = measured('def choose(a,b,c):\n if a:\n  if b:\n   if c: return 1\n return 0\n')[0]
    guards = measured('def choose(a,b,c):\n if not a: return 0\n if not b: return 0\n if not c: return 0\n return 1\n')[0]
    assert nested['health']['readabilityScore'] == 88
    assert guards['health']['readabilityScore'] == 100
    assert compare_readability(nested, guards)['delta'] == 12
    incompatible = deepcopy(guards)
    incompatible['health']['readability']['version'] = 'future-version'
    assert compare_readability(nested, incompatible)['delta'] is None


def test_partial_legacy_and_broken_syntax_cannot_become_good_r(monkeypatch):
    import atlas.code_complexity as complexity
    monkeypatch.setattr(complexity, 'MAX_AST_NODES', 3)
    partial = measured('def read(a,b,c):\n return a and b and c\n')[0]
    assert partial['health']['readabilityScore'] is None
    assert partial['complexity']['halstead']['status'] == 'partial'
    legacy = {'status':'complete', 'maxNestingDepth':0, 'statementCount':1, 'branchCount':0}
    assert assess_readability(legacy, [])['score'] is None
    assert assess_readability(legacy, [])['verdict'] == 'UNKNOWN'
    invalid = measured('def broken(a):\n if: return a\n')[0]
    assert invalid['health']['readabilityScore'] is None


def test_deferred_inventory_and_summary_denominators_are_not_hidden():
    nodes = measured('def configure(a,b,c,d,e):\n def callback():\n  return a and b or c and d or e\n return callback\n')
    assert [node['health']['readabilityScore'] for node in nodes] == [100, 88]
    assert nodes[0]['complexity']['halstead']['excludedDeferredBodies'] == 1
    legacy = {'id':'legacy', 'complexity':{}, 'health':{}}
    summary = summarize([*nodes, nodes[0], legacy])['readability']
    assert summary['assessedMethods'] == 2 and summary['totalMethods'] == 3
    assert summary['meanScore'] == 94 and summary['coveragePercent'] == 66.7


def test_versions_are_not_averaged_and_invalid_numbers_stay_unknown():
    """A mixed historical profile cannot acquire an apparently precise aggregate."""
    nodes = measured('def first(x):\n return x\ndef second(x):\n return x + 1\n')
    nodes[1]['health']['readability']['version'] = 'different-policy'
    assert summarize(nodes)['readability']['meanScore'] is None
    for value in (None, -1, float('nan'), float('inf'), True):
        metrics = deepcopy(nodes[0]['complexity'])
        metrics['maxNestingDepth'] = value
        assert assess_readability(metrics, [])['score'] is None


def test_oversized_record_keeps_r_policy_and_human_review_boundary():
    """Abbreviated MCP rows retain the meaning of an otherwise contextless R scalar."""
    from atlas.code_health_record_page import health_record_page
    node = measured('def first(x):\n return x\n')[0]
    node['health']['findings'] = [{'message':'x' * 100000}]
    page = health_record_page({'nodes':[node]}, 'test-record', 'assessment', 0, 10, '/record')
    item = page['items'][0]
    assert item['itemTruncated']
    assert item['health']['readability']['version'] == 'atlas-readability-v1'
    assert item['health']['readability']['humanReview'] == 'not-performed'


def test_legacy_card_with_null_health_keeps_an_empty_compact_contract():
    """Old graph cards can explicitly store null instead of omitting health."""
    assert compact_health({'id':'legacy','health':None}) == {}
