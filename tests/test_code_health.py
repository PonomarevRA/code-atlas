"""Real grammars and stored snapshots verify health/forecast evidence and isolation."""

from copy import deepcopy
import hashlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from atlas.code_health import calculate_health, enrich_code_health, resolve_inherited_documentation
from atlas.code_health_source import parse_health_source
from atlas.code_health_service import CodeHealthService
from atlas.code_health_models import HealthQuery, HealthPlanQuery
from atlas.code_health_planning import plan_changes
from atlas.code_health_comparison import compare_assessments
from atlas.server import create_app
from atlas import store
from test_grouping_cache import legacy_store

PYTHON = '''def doubled(x):
    """Calculate a doubled amount.

    Args:
        x: Input amount.
    Returns:
        Doubled amount.
    """
    return x * 2
'''


def measured(filename, content):
    """Use real parsers; standalone no-call methods have a proved zero fan-out."""
    nodes = parse_health_source(filename, content.encode())["methods"]
    graph = {"nodes": nodes, "edges": [], "metadata": {}}
    return enrich_code_health(graph)["nodes"]


def test_documentation_is_owned_by_method_and_not_comment_volume():
    source = '''/// <summary>Class purpose does not describe its methods.</summary>
class A {
 int Hidden(int x) { if (x > 1) return x; return 0; }
 /// <summary>TODO</summary>
 int Placeholder(int x) { return x * 2; }
}'''
    hidden, placeholder = measured("a.cs", source)
    assert hidden["documentationHealth"]["documented"] is False
    assert hidden["health"]["documentationScore"] == 0
    assert placeholder["documentationHealth"]["documented"] is False
    assert any(f["rule"] == "placeholder-documentation" for f in placeholder["health"]["findings"])


@pytest.mark.parametrize("filename,source", [
    ("a.py", PYTHON),
    ("a.cs", '''class A { /// <summary>Calculate doubled amount.</summary>
/// <param name="x">Input amount.</param>
/// <returns>Doubled amount.</returns>
private int Doubled(int x) { return x * 2; } }'''),
    ("a.java", '''class A { /** Calculate doubled amount.
 * @param x Input amount.
 * @return Doubled amount. */
private int doubled(int x) { return x * 2; } }'''),
    ("a.js", '''/** Calculate doubled amount.
 * @param x Input amount.
 * @returns Doubled amount. */
function doubled(x) { return x * 2; }'''),
    ("a.ts", '''/** Calculate doubled amount.
 * @param x Input amount.
 * @returns Doubled amount. */
function doubled(x: number): number { return x * 2; }'''),
    ("a.go", '''package a
// Double returns a doubled amount for x.
func Double(x int) int { return x * 2 }'''),
])
def test_private_and_public_documentation_adapters(filename, source):
    node = measured(filename, source)[0]
    assert node["documentationHealth"]["score"] == 100
    assert node["health"]["score"] == 100
    assert node["documentationHealth"]["semanticReview"]["status"] == "not-reviewed"


def test_named_arrow_has_own_docs_and_expression_result():
    source = '/** Doubles x.\n * @param x Input.\n * @returns Doubled value. */\nconst doubled = (x) => x * 2;'
    node = measured("a.js", source)[0]
    assert node["name"] == "doubled"
    assert node["documentationHealth"]["eligible"] is True
    assert node["documentationHealth"]["score"] == 100


def test_overloads_deferred_and_unavailable_are_not_silently_merged():
    nodes = parse_health_source("a.cs", b'class A { int Run(int x) { System.Func<int,int> f = y => y > 0 ? y : 0; return f(x); } int Run(string x) { return x.Length; } }')["methods"]
    assert len({n["healthIdentity"] for n in nodes}) == len(nodes) == 3
    assert nodes[0]["complexity"]["excludedDeferredBodies"] == 1
    assert nodes[0]["complexity"]["branchCount"] == 0
    broken = measured("bad.cs", 'class A { int Broken(int x) { if ( return x; } }')[0]
    assert broken["health"]["score"] is None
    assert broken["documentationHealth"]["documented"] is None
    assert broken["health"]["verdict"] == "UNKNOWN"


def test_inheritdoc_is_resolved_only_against_exact_parent_signature():
    nodes = measured("a.cs", '''interface Parent {
/// <summary>Calculate doubled amount.</summary>
/// <param name="x">Input.</param>
/// <returns>Doubled amount.</returns>
int Run(int x);
}
class Child {
/// <inheritdoc/>
int Run(int x) { return x * 2; }
/// <inheritdoc cref="Other.Run"/>
int Other(int x) { return x * 2; }
}''')
    nodes[0]["memberFacts"] = {"name": "Run", "ownerNodeId": "parent"}
    nodes[1]["memberFacts"] = {"name": "Run", "ownerNodeId": "child"}
    nodes[2]["memberFacts"] = {"name": "Other", "ownerNodeId": "child"}
    resolve_inherited_documentation(nodes, [{"source": "child", "target": "parent", "relation": "implements"}])
    assert nodes[1]["documentationHealth"]["origin"] == "resolved-inherited-contract"
    assert nodes[1]["documentationHealth"]["ownDescription"] is False
    assert nodes[2]["documentationHealth"]["status"] == "partial"


def test_health_never_cancels_critical_nesting_with_docs():
    content = PYTHON.replace('    return x * 2', '\n'.join('    ' * depth + 'if x:' for depth in range(1, 7)) + '\n' + '    ' * 7 + 'return x * 2')
    node = measured("a.py", content)[0]
    assert node["health"]["verdict"] == "REVIEW"
    assert any(f["severity"] == "critical" for f in node["health"]["findings"])
    unknown = calculate_health(node, {"status": "partial", "callTargets": 2, "writes": 0})
    assert unknown["score"] is None


@pytest.fixture
def health_store(tmp_path):
    state, project, path, _ = legacy_store(tmp_path)
    (tmp_path / "amount.py").write_text(PYTHON)
    nodes = measured("amount.py", PYTHON)
    graph = {"nodes": nodes, "edges": [], "metadata": {"snapshotId": "first"}}
    enrich_code_health(graph)
    store.write_json(path, graph)
    try:
        yield state, project, path, tmp_path
    finally:
        state.close()


def test_baseline_forecast_and_comparison_do_not_mutate_source_or_snapshot(health_store, monkeypatch):
    state, project, path, root = health_store
    service = CodeHealthService(state)
    before_source, before_graph = (root / "amount.py").read_bytes(), path.read_bytes()
    def never(*args, **kwargs):
        raise AssertionError("Health must not start graph rebuild")
    monkeypatch.setattr(state, "rebuild", never)
    query = HealthQuery(files=["amount.py"], sourceMode="working-tree")
    before = service.assess(project, query)
    draft = PYTHON.replace("x * 2", "x * 3")
    plan = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=before["assessmentId"], intent="Update amount multiplier", drafts=[{"file": "amount.py", "content": draft}]))
    assert plan["assessmentKind"] == "estimated"
    assert plan["projections"][0]["scoreRange"] == [100, 100]
    assert (root / "amount.py").read_bytes() == before_source and path.read_bytes() == before_graph
    assert draft not in str(list(service.records.directory(project).glob('*.json')))
    for saved in service.records.directory(project).glob('*.json'):
        assert '"content"' not in saved.read_text()
    (root / "amount.py").write_text(draft)
    after = service.assess(project, query)
    compared = compare_assessments(before, after, plan=plan)
    assert compared["planComparison"][0]["withinRange"] is True
    assert after["freshness"]["status"] == "changed-or-unverified"
    assert service.assess(project, HealthQuery(files=["amount.py"]))["nodes"][0]["complexity"]["sourceHash"] == hashlib.sha256(before_source).hexdigest()


def test_forecast_unknown_dependencies_widen_range_and_scenario_assumptions(health_store):
    state, project, _, _ = health_store
    service = CodeHealthService(state)
    baseline = service.assess(project, HealthQuery(files=["amount.py"], sourceMode="working-tree"))
    draft = PYTHON.replace('x * 2', 'external(x)')
    plan = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=baseline["assessmentId"], intent="Delegate amount calculation", drafts=[{"file": "amount.py", "content": draft}]))
    # Unknown binding also leaves cross-file organization unknown: dependencies20% + organization15%.
    assert plan["projections"][0]["scoreRange"] == [65, 100]
    scenario = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=baseline["assessmentId"], intent="Reduce branching", projections=[{"identity": baseline["nodes"][0]["healthIdentity"], "reason": "Replace nested decisions with a guard", "metrics": {"maxNestingDepth": {"min": 0, "max": 1}}}]))
    assert scenario["basis"] == "scenario-assumptions"
    assert scenario["projections"][0]["assumptions"]


def test_report_pages_all_methods_and_global_hotspots(health_store):
    state, project, path, _ = health_store
    graph = state._read_graph(path)
    prototype = graph['nodes'][0]
    graph['nodes'] = [dict(deepcopy(prototype), id=str(i), healthIdentity=str(i), label=str(i), health=dict(prototype['health'], score=100 if i < 130 else 10)) for i in range(150)]
    store.write_json(path, graph)
    service = CodeHealthService(state)
    first = service.report(project, None, limit=40)
    assert first["totalMethods"] == 150
    assert first["nodes"][0]["health"]["score"] == 10
    last = service.report(project, None, limit=40, offset=120)
    assert len(last["nodes"]) == 30 and last["nextCursor"] is None


def test_unknown_added_helpers_and_bad_rename_mapping_are_visible():
    node = measured("a.py", PYTHON)[0]
    before = {"nodes": [node], "files": ["a.py"], "policyVersion": "v1", "analyzerVersion": "v1"}
    extra = deepcopy(node); extra.update(id="new", healthIdentity="new", health={"status": "unavailable", "score": None})
    after = {**before, "nodes": [node, extra]}
    result = compare_assessments(before, after)
    assert result["verdict"] == "UNKNOWN" and result["changes"][-1]["kind"] == "added"
    assert compare_assessments(before, after, identity_map={"nonexistent": "new"})["verdict"] == "UNKNOWN"


def test_http_validates_scope_and_large_draft_without_refresh(health_store):
    state, project, _, root = health_store
    app = create_app(state.data_dir)
    client = TestClient(app, base_url="http://127.0.0.1")
    try:
        baseline = client.post(f"/api/projects/{project}/code-health", json={"files": ["amount.py"], "sourceMode": "working-tree"})
        assert baseline.status_code == 200, baseline.text
        invalid = client.post(f"/api/projects/{project}/code-health", json={"files": ["../outside.py"], "sourceMode": "working-tree"})
        assert invalid.status_code == 403
        symlink = root / 'link.py'; symlink.symlink_to(root.parent / 'outside.py')
        assert client.post(f"/api/projects/{project}/code-health", json={"files": ["link.py"], "sourceMode": "working-tree"}).status_code == 403
        body = {"baselineAssessmentId": baseline.json()["assessmentId"], "intent": "Assess full draft", "drafts": [{"file": "amount.py", "content": '#' + 'A' * 20000 + '\n' + PYTHON}]}
        planned = client.post(f"/api/projects/{project}/code-health/plan", json=body)
        assert planned.status_code == 200, planned.text
        assert planned.json()["assessmentKind"] == "estimated"
        assert (root / "amount.py").read_text() == PYTHON
        assert client.post(f"/api/projects/{project}/code-health/plan", json={**body, "projections": [{"identity": "x", "reason": "y"}]}).status_code == 422
    finally:
        app.state.store.close(); app.state.semantic.close(); app.state.data_maps.close(); app.state.references.close(); app.state.libraries.close()


def test_empty_unsupported_or_broken_sources_do_not_compare_as_healthy(health_store):
    state, project, _, root = health_store
    service = CodeHealthService(state)
    for file, content in [('unknown.rb', 'def run; end'), ('broken.cs', 'class A { int Run(')]:
        (root / file).write_text(content)
        query = HealthQuery(files=[file], sourceMode='working-tree')
        before, after = service.assess(project, query), service.assess(project, query)
        assert before['status'] != 'complete'
        assert before['summary']['logicInventory']['status'] == 'partial'
        assert compare_assessments(before, after)['verdict'] == 'UNKNOWN'
        forecast = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=before['assessmentId'], intent='Assess draft', drafts=[{'file': file, 'content': content}]))
        assert forecast['status'] == 'partial'


def test_unknown_baseline_is_not_pass_when_after_becomes_complete():
    node = measured('a.py', PYTHON)[0]
    before = {'nodes': [deepcopy(node)], 'files': ['a.py'], 'policyVersion': 'v1', 'analyzerVersion': 'v1'}
    before['nodes'][0]['health'].update(status='partial', score=None)
    after = {**before, 'nodes': [node]}
    assert compare_assessments(before, after)['verdict'] == 'UNKNOWN'


def test_branching_alias_is_rejected_in_forecast():
    from pydantic import ValidationError
    from atlas.code_health_models import HealthProjection
    with pytest.raises(ValidationError, match='Unsupported projected metric'):
        HealthProjection(identity='x', reason='Change decisions', metrics={'branchingCount': {'min': 0, 'max': 1}})


def test_documentation_only_changes_reuse_verified_dependency_stamp(health_store):
    state, project, _, root = health_store
    service = CodeHealthService(state)
    baseline = service.assess(project, HealthQuery(files=['amount.py'], sourceMode='working-tree'))
    (root / 'amount.py').write_text(PYTHON.replace('Calculate a doubled amount.', 'Calculate an amount while preserving its unit.'))
    after = service.assess(project, HealthQuery(files=['amount.py'], sourceMode='working-tree'))
    assert after['nodes'][0]['healthSourceStamp'] == baseline['nodes'][0]['healthSourceStamp']
    assert after['nodes'][0]['complexity']['sourceHash'] != baseline['nodes'][0]['complexity']['sourceHash']
    assert after['nodes'][0]['health']['dependencies']['basis'] == 'static-graph'


def test_captured_call_coverage_prevents_false_zero_dependencies():
    source = '''class A {
/// <summary>Read the current price for token.</summary>
/// <param name="token">Cancellation token.</param>
/// <returns>The current price.</returns>
async Task<int> Price(Token token) { return await provider.GetPrice(token); }
}'''
    node = measured('a.cs', source)[0]
    assert node['health']['score'] is None
    assert node['health']['dependencies']['unknownTargets'] == 1
    assert node['health']['verdict'] == 'UNKNOWN'
    target = {'id': 'external-price', 'kind': 'external', 'label': 'Provider.GetPrice()'}
    graph = {'nodes': [node, target], 'edges': [{'source': node['id'], 'target': target['id'], 'relation': 'calls', 'line': 5}], 'metadata': {}}
    assert enrich_code_health(graph)['nodes'][0]['health']['score'] == 100


def test_advisory_ci_gate_only_fails_when_enabled(tmp_path, capsys):
    import json
    from atlas.code_health_gate import main
    node = measured('a.py', PYTHON)[0]
    record = {'nodes': [node], 'files': ['a.py'], 'policyVersion': 'v1', 'analyzerVersion': 'v1'}
    before, after = tmp_path / 'before.json', tmp_path / 'after.json'
    before.write_text(json.dumps(record)); after.write_text(json.dumps(record))
    assert main(['--before', str(before), '--after', str(after)]) == 0
    record['nodes'][0]['health'].update(status='partial', score=None)
    after.write_text(json.dumps(record))
    assert main(['--before', str(before), '--after', str(after), '--fail-on-unknown']) == 1
    capsys.readouterr()


def test_partial_metrics_do_not_become_point_forecast_from_one_assumption(health_store):
    state, project, _, _ = health_store
    service = CodeHealthService(state)
    baseline = service.assess(project, HealthQuery(files=['amount.py'], sourceMode='working-tree'))
    baseline['nodes'][0]['complexity']['status'] = 'partial'
    baseline['nodes'][0]['health']['status'] = 'partial'
    baseline = service.records.save(project, baseline)
    forecast = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=baseline['assessmentId'], intent='Reduce nesting', projections=[{'identity':baseline['nodes'][0]['healthIdentity'],'reason':'Use early return','metrics':{'maxNestingDepth':{'min':0,'max':1}}}]))
    low, high = forecast['projections'][0]['scoreRange']
    assert low < high


def test_working_source_mutual_helpers_retain_recursion(health_store):
    state, project, _, _ = health_store
    service = CodeHealthService(state)
    result = service.evaluate_contents({'nodes': [], 'edges': []}, {'a.py': b'def first(x):\n    return second(x)\ndef second(x):\n    return first(x)\n'})
    assert all(node['health']['dependencies']['recursive'] for node in result['nodes'])


def test_scenario_count_ranges_do_not_resolve_unknown_provider(health_store):
    state, project, _, root = health_store
    (root / 'amount.py').write_text(PYTHON.replace('x * 2', 'provider.get_price(x)'))
    service = CodeHealthService(state)
    baseline = service.assess(project, HealthQuery(files=['amount.py'], sourceMode='working-tree'))
    plan = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=baseline['assessmentId'], intent='Preserve provider call', projections=[{'identity':baseline['nodes'][0]['healthIdentity'],'reason':'Expect one call without writes','metrics':{'callTargets':{'min':1,'max':1},'writes':{'min':0,'max':0}}}]))
    assert plan['projections'][0]['scoreRange'] == [65, 100]


def test_bare_call_cannot_bind_method_of_an_unrelated_class(health_store):
    state, _, _, _ = health_store
    source = b'class A : ExternalBase { int Run() { return Price(); } } class B { int Price() { return 1; } }'
    result = CodeHealthService(state).evaluate_contents({'nodes':[], 'edges':[]}, {'a.cs':source})
    run = next(n for n in result['nodes'] if n['name'] == 'Run')
    assert run['health']['dependencies']['status'] == 'partial'
    assert run['health']['dependencies']['unknownTargets'] == 1


def test_local_function_binding_precedes_module_function(health_store):
    state, _, _, _ = health_store
    source = b'def work(x):\n    def helper(x):\n        return x * 2\n    return helper(x)\ndef helper(x):\n    return external(x)\n'
    result = CodeHealthService(state).evaluate_contents({'nodes':[], 'edges':[]}, {'a.py':source})
    assert next(n for n in result['nodes'] if n['name'] == 'work')['health']['dependencies']['status'] == 'complete'


def test_changed_inheritance_cannot_reuse_captured_inheritdoc(health_store):
    state, _, _, _ = health_store
    source = '''interface I {
/// <summary>Calculate the I result.</summary>
/// <param name="x">Input.</param>
/// <returns>I amount.</returns>
int Run(int x);
}
class A : I {
/// <inheritdoc/>
int Run(int x) { return x * 2; }
}'''
    nodes = measured('a.cs', source)
    for node, owner in zip(nodes, ['I', 'A']):
        node['memberFacts'] = {'name': 'Run', 'ownerNodeId': owner}
    graph = {'nodes':nodes + [{'id':'I','label':'I'},{'id':'A','label':'A'}], 'edges':[{'source':'A','target':'I','relation':'implements'}]}
    service = CodeHealthService(state)
    current = service.evaluate_contents(graph, {'a.cs':source.replace('class A : I', 'class A : J').encode()})
    child = next(node for node in current['nodes'] if node.get('memberFacts', {}).get('ownerNodeId') == 'A')
    assert child['documentationHealth']['status'] == 'partial'
    assert child['documentationHealth']['origin'] == 'declaration'
