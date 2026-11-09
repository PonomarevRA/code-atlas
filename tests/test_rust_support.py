"""Настоящая грамматика Rust проверяет метрики, rustdoc, Cargo и неизменяемые MCP-факты."""

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from tree_sitter import Language, Parser
import tree_sitter_rust

from atlas.code_complexity import analyze_complexity
from atlas.code_health import enrich_code_health
from atlas.code_health_source import parse_health_source
from atlas.graph_context import read_graph_node
from atlas.member_graph import enrich_member_graph
from atlas.mcp_graph import build_mcp_graph
from atlas.project_scopes import assign_code_projects, UNASSIGNED_PROJECT


def rust_metrics(source):
    """Измеряет первое тело функции из реального AST без запуска Rust-кода."""
    content = source.encode()
    tree = Parser(Language(tree_sitter_rust.language())).parse(content)
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type == 'function_item':
            return analyze_complexity(node, content, 'rust', source_kind='test-source',
                                      source_hash=hashlib.sha256(content).hexdigest())
        pending.extend(reversed(node.named_children))
    raise AssertionError('Fixture must contain a function')


def test_rust_controls_guards_and_try_are_local_structural_decisions():
    """Ветки match, его guard и ? учитываются без выдуманного результата или runtime-глубины."""
    match = rust_metrics('fn choose(x:i32)->i32 { match x { 1 if x>0 => 1, 2=>2, _=>3 } }')
    assert (match['branchCount'], match['maxNestingDepth'], match['score']) == (3, 1, 4)
    assert match['status'] == 'complete' and match['languageRulesVersion'] == 'rust-structure-v1'
    tried = rust_metrics('fn read()->Result<i32,E> { let n = load()?; Ok(n) }')
    assert tried['shortCircuitCount'] == 1 and tried['maxNestingDepth'] == 0
    assert tried['statementCount'] == 2
    flat = rust_metrics('fn choose(a:bool,b:bool)->i32 { if a {1} else if b {2} else {3} }')
    assert flat['maxNestingDepth'] == 1 and flat['branchCount'] == 2
    let_else = rust_metrics('fn choose(x:Option<i32>)->i32 { let Some(n)=x else {return 0;}; n }')
    assert let_else['branchCount'] == 1 and let_else['maxNestingDepth'] == 1


def test_rust_loops_let_conditions_and_deferred_code_keep_their_scopes():
    """for/while/loop не смешиваются с телами closure, async и локальной функции."""
    source = '''fn run(xs:&[i32], value:Option<i32>) {
 let later = |x| if x>0 { 1 } else { 0 };
 let future = async { loop { break; } };
 fn local(){ loop { break; } }
 for x in xs { if *x>0 { continue; } }
 while let Some(x)=value { if x>0 { break; } }
 loop { break; }
}'''
    result = rust_metrics(source)
    assert result['loopCount'] == 3 and result['branchCount'] == 2
    assert result['maxNestingDepth'] == 2 and result['excludedDeferredBodies'] == 3
    methods = parse_health_source('src/lib.rs', source.encode())['methods']
    assert {'method', 'deferred'} <= {n['kind'] for n in methods}
    assert len({n['healthIdentity'] for n in methods}) == len(methods)


def test_rust_macros_syntax_and_budgets_do_not_claim_exact_health(monkeypatch):
    """Макросы не расширяются; их скрытая логика не получает ложный полный балл."""
    source = b'fn run(){ println!("if while"); }'
    parsed = parse_health_source('src/main.rs', source)
    graph = enrich_code_health({'nodes': parsed['methods'], 'edges': [], 'metadata': {}})
    node = graph['nodes'][0]
    assert node['complexity']['status'] == 'partial'
    assert node['complexity']['averageNestingDepth'] is None
    assert node['complexity']['unsupported'] == ['macro expansion unavailable']
    assert node['health']['score'] is None
    assert node['healthCallFacts']['calls'][0]['expression'] == 'println!'
    broken = rust_metrics('fn broken(){ let x = ; }')
    assert broken['status'] == 'unavailable' and broken['score'] is None
    monkeypatch.setattr('atlas.code_complexity.MAX_AST_NODES', 2)
    partial = rust_metrics('fn run(){ if a { for x in xs { work(); } } }')
    assert partial['truncated'] and partial['averageNestingDepth'] is None


def test_rustdoc_attributes_and_trait_impl_identities_are_preserved(tmp_path):
    """Одноимённые методы разных impl/module не сливаются; rustdoc принадлежит своему объявлению."""
    source = '''//! Module notes must not become a function's purpose.
/// Returns twice value.
/// # Arguments
/// * `value` - Input amount.
/// # Returns
/// Doubled amount.
/// # Examples
/// Extra prose is not part of the contract.
#[inline]
pub fn doubled(value:i32)->i32 { value*2 }
trait T { fn read(&self)->i32; }
struct A { count:i32 }
struct B { count:i32 }
impl A { fn read(&self)->i32 { self.count } }
impl T for A { fn read(&self)->i32 { self.count } }
impl T for B { fn read(&self)->i32 { self.count } }
mod child { fn read()->i32 { 1 } }
'''
    path = tmp_path / 'lib.rs'
    path.write_text(source)
    parsed = parse_health_source('lib.rs', source.encode())
    assert parsed['status'] == 'complete'
    assert len({n['healthIdentity'] for n in parsed['methods']}) == len(parsed['methods'])
    doubled = parsed['methods'][0]
    assert doubled['documentationHealth']['score'] == 100
    assert doubled['documentationHealth']['purpose'] == 'Returns twice value.'
    assert all(n['documentationHealth']['parameterNames'] == [] for n in parsed['methods'][1:])
    graph = enrich_member_graph({'nodes': [{'id':'file', 'file':'lib.rs', 'label':'lib.rs', 'kind':'file'}],
                                 'edges': [], 'metadata': {}}, tmp_path)
    captured = next(n for n in graph['nodes'] if n.get('memberFacts', {}).get('name') == 'doubled')
    assert 'Returns twice value.' in captured['callableFacts']['description']
    assert 'Module notes' not in captured['callableFacts']['description']
    assert captured['healthIdentity'] == doubled['healthIdentity']
    before = deepcopy(graph)
    mcp = next(n for n in build_mcp_graph(graph)['nodes'] if n['id'] == captured['id'])
    assert mcp['complexity']['language'] == 'rust'
    path.unlink()
    assert read_graph_node(graph, captured['id'])['node']['documentationHealth']['documented']
    assert graph == before


def test_rust_writes_and_bodyless_trait_contracts_remain_explicit():
    """Мутация поля видна в working-tree, а объявление трейта не выдаётся за пустую реализацию."""
    source = b'trait T { fn run(&self); } struct A { n:i32 } impl A { fn run(&mut self){self.n += 1;} }'
    methods = parse_health_source('src/lib.rs', source)['methods']
    assert methods[0]['complexity']['status'] == 'unavailable'
    assert methods[0]['complexity']['score'] is None
    assert methods[1]['lexicalWrites'] == 1


def test_rust_destructured_inputs_do_not_claim_complete_documentation():
    """Неподдержанный список имён pattern остаётся partial, даже при наличии собственного описания."""
    source = b'/// Returns the sum.\nfn sum((a,b):(i32,i32))->i32 {a+b}'
    node = parse_health_source('src/lib.rs', source)['methods'][0]
    assert node['documentationHealth']['status'] == 'partial'
    assert node['documentationHealth']['documented'] is None


def test_cargo_projects_use_nearest_saved_manifest_without_path_prefix_guessing():
    """Crates workspace получают разные группы; похожий путь и внешние узлы не присваиваются crate."""
    nodes = [{'id':'workspace', 'file':'Cargo.toml'}, {'id':'root', 'file':'src/lib.rs'},
             {'id':'crate', 'file':'crates/core/Cargo.toml'}, {'id':'method', 'file':'crates/core/src/lib.rs'},
             {'id':'other', 'file':'crates/core-other/src/lib.rs'},
             {'id':'external', 'file':'crates/core/src/lib.rs', 'kind':'external'}]
    projects = assign_code_projects(nodes)
    by_id = {n['id']:n['codeProjectId'] for n in nodes}
    assert by_id['workspace'] == by_id['root'] == by_id['other']
    assert by_id['crate'] == by_id['method'] != by_id['root']
    assert by_id['external'] == UNASSIGNED_PROJECT
    assert {p['manifest'] for p in projects if 'manifest' in p} == {'Cargo.toml', 'crates/core/Cargo.toml'}


def test_rust_working_tree_planning_and_measurement_use_the_same_method_identity(tmp_path):
    """Оценка и прогноз Rust используют общий сервис; черновик никогда не записывается в проект."""
    from atlas.code_health_service import CodeHealthService
    from atlas.code_health_models import HealthQuery, HealthPlanQuery
    from atlas.code_health_planning import plan_changes
    from test_grouping_cache import legacy_store

    state, project, _, _ = legacy_store(tmp_path)
    source = '/// Returns twice value.\npub fn doubled(value:i32)->i32 { value*2 }\n'
    file = tmp_path / 'lib.rs'
    file.write_text(source)
    try:
        service = CodeHealthService(state)
        baseline = service.assess(project, HealthQuery(sourceMode='working-tree', files=['lib.rs']))
        assert baseline['status'] == 'complete' and baseline['nodes'][0]['health']['score'] == 100
        proposed = source.replace('value*2', 'if value>0 {value*2} else {0}')
        plan = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=baseline['assessmentId'],
                            intent='Preserve nonpositive handling explicitly', drafts=[{'file':'lib.rs', 'content':proposed}]))
        assert plan['assessmentKind'] == 'estimated' and plan['projections']
        assert file.read_text() == source
        assert parse_health_source('lib.rs', proposed.encode())['methods'][0]['healthIdentity'] == baseline['nodes'][0]['healthIdentity']
    finally:
        state.close()


def test_graphify_rust_cargo_and_atlas_mcp_pipeline_do_not_execute_the_project(tmp_path):
    """Настоящее извлечение публикует Cargo/Rust-факты, не вызывая cargo, build.rs или Rust-программу."""
    from atlas.graphify_adapter import build_graph
    from atlas.enrichment_pipeline import enrich_snapshot
    from atlas.grouping import enrich_graph

    root = tmp_path / 'rust-project'
    (root / 'src').mkdir(parents=True)
    (root / 'Cargo.toml').write_text('[package]\nname="atlas-rust-fixture"\nversion="0.1.0"\nedition="2021"\n')
    (root / 'src/lib.rs').write_text('/// Returns twice value.\npub fn doubled(value:i32)->i32 { value*2 }\n')
    (root / 'build.rs').write_text('fn main(){ std::fs::write("executed.txt", "must not run").unwrap(); }')
    before = {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    raw = build_graph(root, tmp_path / 'graph-data')
    raw_copy = deepcopy(raw)
    vendor_path = Path(raw['metadata']['rawGraphPath'])
    vendor_bytes = vendor_path.read_bytes()
    graph = enrich_graph(enrich_snapshot(raw, root))
    method = next(n for n in graph['nodes'] if n.get('memberFacts', {}).get('name') == 'doubled')
    assert method['complexity']['language'] == 'rust' and method['complexity']['status'] == 'complete'
    assert method['documentationHealth']['documented']
    project = next(p for p in graph['codeProjects'] if p.get('manifest') == 'Cargo.toml')
    assert method['codeProjectId'] == project['id']
    mcp = next(n for n in build_mcp_graph(graph)['nodes'] if n['id'] == method['id'])
    assert mcp['complexity']['score'] == 0
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    assert raw['nodes'] == raw_copy['nodes'] and raw['edges'] == raw_copy['edges']
    assert vendor_path.read_bytes() == vendor_bytes
