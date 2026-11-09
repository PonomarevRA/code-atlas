"""Real syntax verifies local depth, alternatives and immutable snapshot/MCP metrics."""
from copy import deepcopy
import hashlib
import importlib

import pytest
from tree_sitter import Language, Parser
from atlas.code_complexity import analyze_complexity
from atlas.interface_source import walk
from atlas.node_description import build_node_description
from atlas.graph_context import read_graph_neighbors, read_graph_node
from atlas.agent_views import search_nodes
from atlas.mcp_graph import build_mcp_graph
from tests.test_interface_dispatch import fixture
from tests.test_member_graph import capture


def metrics(source, language='csharp', method_type='method_declaration'):
    """Use the same installed grammar and AST contract as graph enrichment."""
    module = importlib.import_module('tree_sitter_' + ('c_sharp' if language == 'csharp' else language))
    get_language = module.language_typescript if language == 'typescript' else module.language
    content = source.encode()
    tree = Parser(Language(get_language())).parse(content)
    declaration = next(n for n in walk(tree.root_node) if n.type == method_type)
    return analyze_complexity(declaration, content, language, source_kind='test-source', source_hash=hashlib.sha256(content).hexdigest())


def test_empty_and_straight_methods_have_zero_depth():
    result = metrics('class C { int M(){ var s="if for while ?"; return 1; } }')
    assert result['score'] == 0 and result['maxNestingDepth'] == 0
    assert result['averageNestingDepth'] == 0 and result['statementCount'] == 2
    assert result['status'] == 'complete'


def test_depth_and_average_use_lexical_statements_not_ast_or_call_depth():
    result = metrics('class C { void M(){ Setup(); if(a){ for(;;){ Work(); } } Finish(); } }')
    assert result['maxNestingDepth'] == 2
    assert result['averageNestingDepth'] == 1.0  # (0 + 1 + 2 + 2 + 0) / 5
    assert (result['branchingCount'], result['loopCount'], result['score']) == (1, 1, 4)


def test_else_if_does_not_increase_depth_but_true_nested_if_does():
    flat = metrics('class C { void M(){ if(a) A(); else if(b) B(); else Cc(); } }')
    nested = metrics('class C { void M(){ if(a){ if(b) B(); } } }')
    assert flat['maxNestingDepth'] == 1 and flat['branchingCount'] == 2
    assert nested['maxNestingDepth'] == 2


def test_value_operators_are_decisions_and_boolean_chains_do_not_fake_scope_depth():
    result = metrics('class C { object M(){ var x = a?.B ?? z; if(p && q || r) F(); return ok ? x : z; } }')
    assert result['shortCircuitCount'] == 4
    assert result['branchCount'] == 2 and result['maxNestingDepth'] == 1
    assert result['score'] == 7


def test_switch_alternatives_and_deferred_bodies():
    result = metrics('class C { int M(){ var f = ()=>{if(a){while(b){ F(); }}}; int Local(){if(a)return 1;return 0;} return x switch { 1=>2, 2=>3, _=>4 }; } }')
    assert result['branchingCount'] == 2 and result['loopCount'] == 0
    assert result['maxNestingDepth'] == 1 and result['excludedDeferredBodies'] == 2


def test_partial_and_bodyless_do_not_claim_exact_average_or_zero(monkeypatch):
    import atlas.code_complexity as module
    monkeypatch.setattr(module, 'MAX_AST_NODES', 2)
    partial = metrics('class C { void M(){ if(a){for(;;){F();}} } }')
    assert partial['status'] == 'partial' and partial['averageNestingDepth'] is None and partial['truncated']
    unknown = metrics('interface I { int M(); }')
    assert unknown['status'] == 'unavailable' and unknown['score'] is None


def test_syntax_errors_are_unknown_instead_of_a_recovery_ast_score():
    result = metrics('class C { void M(){ if () { while() { Work(); } } } }')
    assert result['status'] == 'unavailable' and result['score'] is None
    assert result['reason'] == 'syntax errors' and result['sourceHash']


@pytest.mark.parametrize('suffix,language,source', [
    ('.py','python','def Run():\n    if a:\n        while b:\n            work()\n'),
    ('.java','java','class C { void Run(){ if(a){ while(b){ work(); } } } }'),
    ('.js','javascript','class C { Run(){ if(a){ while(b){ work(); } } } }'),
    ('.ts','typescript','class C { Run(){ if(a){ while(b){ work(); } } } }'),
    ('.go','go','package p\nfunc Run(){ if a { for b { work() } } }'),
])
def test_metrics_survive_publication_search_neighbors_and_missing_source(tmp_path, suffix, language, source):
    graph = capture(tmp_path, suffix, source)
    node = next(n for n in graph['nodes'] if n.get('memberFacts',{}).get('name') == 'Run')
    facts = node['complexity']
    assert (facts['language'], facts['maxNestingDepth'], facts['loopCount'], facts['score']) == (language, 2, 1, 4)
    original = deepcopy(graph)
    assert search_nodes(graph, 'Run')['nodes'][0]['complexity'] == facts
    neighbors = read_graph_neighbors(graph, node['memberFacts']['ownerNodeId'])
    assert next(n for n in neighbors['nodes'] if n['id'] == node['id'])['complexity'] == facts
    mcp = next(n for n in build_mcp_graph(graph)['nodes'] if n['id'] == node['id'])
    assert mcp['attributes']['atlas.0.complexity']['score'] == 4
    assert mcp['complexity'] == facts and graph == original
    (tmp_path / ('Code'+suffix)).unlink()
    assert read_graph_node(graph, node['id'])['node']['complexity'] == facts


def test_csharp_snapshot_and_fresh_description_share_formula(tmp_path):
    source = 'class C { void Run(){ if(a){ while(b){ Work(); } } } }'
    graph, _ = fixture(tmp_path, source)
    from atlas.interface_dispatch import enrich_interface_dispatch
    graph = enrich_interface_dispatch(graph, tmp_path)
    node = next(n for n in graph['nodes'] if n.get('label') == '.Run()')
    result = build_node_description(tmp_path, graph, node['id'], 's')
    assert result['complexity']['score'] == node['complexity']['score'] == 4
    assert result['complexity']['sourceKind'] == 'working-tree'
    assert node['complexity']['sourceKind'] == 'snapshot-captured-source'
