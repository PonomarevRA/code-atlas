"""Real health integration plus adversarial static inventories for file/folder organization."""

from copy import deepcopy
import pytest

from atlas.code_health import enrich_code_health, compact_health
from atlas.code_health_service import CodeHealthService
from atlas.code_health_comparison import compare_assessments
from atlas.code_health_planning import plan_changes
from atlas.code_health_models import HealthQuery, HealthPlanQuery
from atlas.code_organization import organization_profiles, organization_summary
from atlas.code_health_mcp import bounded_health
from test_code_health import PYTHON, health_store


def declaration(identifier, filename, statements=1):
    """Supply complete local metrics without inferring any semantic responsibility."""
    return {'id': identifier, 'healthIdentity': identifier, 'file': filename, 'label': identifier,
            'complexity': {'status': 'complete', 'statementCount': statements, 'branchingCount': 0}}


def profiles(nodes, pairs=(), **kwargs):
    """Known call edges are explicit test facts; duplicate edges must not grow coupling."""
    dependencies = {node['id']: {'status': 'complete'} for node in nodes}
    edges = [{'source': a, 'target': b, 'relation': 'calls'} for a, b in pairs]
    return organization_profiles(nodes, edges, dependencies, **kwargs)


def rules(profile):
    return {finding['rule'] for finding in profile['findings']}


def test_logic_concentration_is_per_file_not_average_method_size():
    nodes = [declaration(str(i), 'app/huge.py', 20) for i in range(35)]
    result = profiles(nodes)
    assert result['app/huge.py']['metrics']['statements'] == 700
    assert {'large-file', 'many-file-declarations'} <= rules(result['app/huge.py'])
    assert result['app/huge.py']['score'] < 100
    # Duplicate diagram occurrences cannot increase the file inventory.
    assert profiles(nodes + [deepcopy(nodes[0])]) == result


def test_repeated_declarations_reuse_paths_and_keep_cross_scope_targets(monkeypatch):
    """Large files resolve paths once while external selected-scope targets stay in coupling counts."""
    import atlas.code_organization as organization
    calls = []
    original = organization.source_file

    def track_path(node):
        calls.append(node.get('file'))
        return original(node)

    monkeypatch.setattr(organization, 'source_file', track_path)
    nodes = [declaration(str(i), 'domain/main.py', 4) for i in range(2000)]
    nodes += [{'id': 'target', 'file': 'other/library.py'}]
    pairs = [(str(i), 'target') for i in range(2000)]
    result = profiles(nodes, pairs)
    assert calls == ['domain/main.py', 'other/library.py']
    assert result['domain/main.py']['metrics']['declarations'] == 2000
    assert result['domain/main.py']['metrics']['outgoingFiles'] == 1
    assert result['domain/main.py']['folderMetrics']['outgoingFolders'] == 1
    assert list(result) == ['domain/main.py']


def test_parent_depth_and_independent_small_files_are_not_bad_structure():
    shallow = profiles([declaration('one', 'one.py')])['one.py']
    deep = profiles([declaration('one', 'domain/payments/contracts/v1/one.py')])['domain/payments/contracts/v1/one.py']
    assert shallow['score'] == deep['score'] == 100
    assert deep['folderMetrics']['depth'] == 4
    independent = profiles([declaration(str(i), f'dto/{i}.py') for i in range(8)])
    assert all('file-fragmentation' not in rules(value) for value in independent.values())


def test_connected_tiny_files_are_reviewed_instead_of_rewarded_for_splitting():
    nodes = [declaration(str(i), f'work/{i}.py') for i in range(8)]
    result = profiles(nodes, [(str(i), str(i + 1)) for i in range(7)])
    assert all('file-fragmentation' in rules(row) for row in result.values())
    assert organization_summary(result)['score'] == 85
    assert organization_summary(result)['hotspotsTruncated']
    # Moving each forwarding fragment to a new folder cannot hide the signal.
    spread = [declaration(str(i), f'feature{i}/part.py') for i in range(8)]
    assert all('file-fragmentation' in rules(row) for row in profiles(spread, [(str(i), str(i + 1)) for i in range(7)]).values())


def test_file_and_folder_cycles_use_resolved_calls_only():
    nodes = [declaration('a', 'input/a.py'), declaration('b', 'output/b.py')]
    result = profiles(nodes, [('a', 'b'), ('b', 'a'), ('a', 'b')])
    assert result['input/a.py']['metrics']['outgoingFiles'] == 1
    assert result['input/a.py']['folderMetrics']['outgoingFolders'] == 1
    assert result['input/a.py']['folderMetrics']['inCycle']
    assert 'organization-cycle' in rules(result['input/a.py'])
    assert profiles(nodes, [('a', 'missing')])['input/a.py']['metrics']['outgoingFiles'] == 0
    dependencies = {node['id']: {'status': 'complete'} for node in nodes}
    dispatch = organization_profiles(nodes, [{'source': 'a', 'target': 'b', 'relation': 'dispatches_to'}], dependencies)
    assert dispatch['input/a.py']['metrics']['outgoingFiles'] == 0


def test_folder_counts_and_cross_folder_cycles_are_separate_from_file_cycles():
    crowded = profiles([declaration(str(i), f'feature/{i}.py', 4) for i in range(26)])
    assert 'crowded-folder' in rules(crowded['feature/0.py'])
    nodes = [declaration('a', 'one/a.py'), declaration('b', 'two/b.py'),
             declaration('c', 'two/c.py'), declaration('d', 'one/d.py')]
    result = profiles(nodes, [('a', 'b'), ('c', 'd')])
    assert not any(row['metrics']['inCycle'] for row in result.values())
    assert all(row['folderMetrics']['inCycle'] for row in result.values())


@pytest.mark.parametrize('mode', ['partial-metrics', 'unresolved', 'truncated'])
def test_incomplete_facts_never_become_healthy_zero_dependencies(mode):
    nodes = [declaration('a', 'a.py')]
    deps = {'a': {'status': 'partial' if mode == 'unresolved' else 'complete'}}
    if mode == 'partial-metrics':
        nodes[0]['complexity']['status'] = 'partial'
    result = organization_profiles(nodes, [], deps, complete=mode != 'truncated')
    assert result['a.py']['score'] is None
    assert organization_summary(result)['unknownFiles'] == 1


def test_empty_removed_draft_does_not_poison_remaining_folder():
    result = profiles([declaration('a', 'src/a.py')], files={
        'src/a.py': {'status': 'complete'}, 'src/removed.py': {'status': 'complete', 'empty': True}})
    assert list(result) == ['src/a.py']
    assert result['src/a.py']['score'] == 100


def test_organization_requires_scope_not_method_replication():
    rows = profiles([declaration('a', 'one.py'), declaration('b', 'one.py'), declaration('c', 'two.py')])
    summary = organization_summary(rows)
    assert summary['fileCount'] == 2 and summary['folderCount'] == 1
    assert 'selected-files' in summary['scope']


def test_real_health_mcp_and_compact_nodes_include_organization_and_transparency(health_store):
    state, project, _, _ = health_store
    service = CodeHealthService(state)
    result = service.assess(project, HealthQuery(files=['amount.py'], sourceMode='working-tree'))
    health = result['nodes'][0]['health']
    assert result['policyVersion'] == 'atlas-health-v3'
    assert health['score'] == health['organizationScore'] == health['transparencyScore'] == 100
    assert result['summary']['organization']['fileCount'] == 1
    assert result['summary']['transparency']['score'] == 100
    assert compact_health(result['nodes'][0])['health']['organizationScore'] == 100
    assert bounded_health(result)['nodes'][0]['health']['organization']['folder'] == '.'


def test_transparency_keeps_control_flow_in_its_score(health_store):
    state, _, _, _ = health_store
    source = PYTHON.replace('    return x * 2', '\n'.join('    ' * depth + 'if x:' for depth in range(1, 7)) + '\n' + '    ' * 7 + 'return x * 2')
    result = CodeHealthService(state).evaluate_contents({'nodes': [], 'edges': []}, {'nested.py': source.encode()})
    assert result['nodes'][0]['health']['transparencyScore'] < 100
    assert result['nodes'][0]['health']['verdict'] == 'REVIEW'


def test_draft_addition_recomputes_unchanged_files_in_same_folder(health_store):
    state, project, _, root = health_store
    (root / 'another.py').write_text(PYTHON.replace('doubled', 'tripled'))
    service = CodeHealthService(state)
    baseline = service.assess(project, HealthQuery(files=['amount.py', 'another.py'], sourceMode='working-tree'))
    plan = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=baseline['assessmentId'], intent='Add a peer file',
            drafts=[{'file': 'third.py', 'content': PYTHON.replace('doubled', 'fourth')}]))
    assert plan['projectedSummary']['organization']['fileCount'] == 3
    assert len(plan['projections']) == 3
    assert all(row['organizationScore'] == 100 for row in plan['projections'])
    assert not (root / 'third.py').exists()


def test_scenario_forecasts_keep_file_organization_as_an_explicit_assumption(health_store):
    state, project, _, _ = health_store
    service = CodeHealthService(state)
    baseline = service.assess(project, HealthQuery(files=['amount.py'], sourceMode='working-tree'))
    plan = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=baseline['assessmentId'], intent='Keep simple structure',
            projections=[{'identity': baseline['nodes'][0]['healthIdentity'], 'reason': 'No structural move is proposed'}]))
    assert plan['projections'][0]['organizationScore'] == 100
    assert plan['projections'][0]['transparencyRange'] == [100, 100]
    assert any('organization-assumed-unchanged' in item['rule'] for item in plan['assumptions'])


def test_changed_baseline_source_keeps_forecast_context_unknown(health_store):
    state, project, _, root = health_store
    service = CodeHealthService(state)
    baseline = service.assess(project, HealthQuery(files=['amount.py'], sourceMode='working-tree'))
    (root / 'amount.py').write_text(PYTHON.replace('x * 2', 'x * 5'))
    plan = plan_changes(service, project, HealthPlanQuery(baselineAssessmentId=baseline['assessmentId'], intent='Add another file',
            drafts=[{'file': 'new.py', 'content': PYTHON}]))
    assert plan['status'] == 'partial'
    assert all(row['organizationScore'] is None for row in plan['projections'])
    assert any('organization-unavailable' in item['rule'] for item in plan['assumptions'])


def test_policy_migration_cannot_claim_health_improvement():
    assert compare_assessments({'policyVersion': 'atlas-health-v1'}, {'policyVersion': 'atlas-health-v2'})['verdict'] == 'UNKNOWN'


def test_context_delta_is_visible_even_when_method_text_is_unchanged(health_store):
    state, project, _, _ = health_store
    service = CodeHealthService(state)
    before = service.assess(project, HealthQuery(files=['amount.py'], sourceMode='working-tree'))
    after = deepcopy(before)
    health = after['nodes'][0]['health']
    health['organizationScore'] = 80
    health['transparencyScore'] = 92
    health['organization']['score'] = 80
    result = compare_assessments(before, after)
    assert result['changes'][0]['organizationDelta'] == -20
    assert result['changes'][0]['transparencyDelta'] == -8
    assert result['changes'][0]['contextChanged']


def test_unknown_call_preserves_local_facts_but_limits_transitive_scope():
    """One unknown source cannot prove incoming/cycle absence for any other selected folder."""
    nodes = [declaration('a', 'one/a.py', 4), declaration('b', 'two/b.py', 8)]
    result = organization_profiles(nodes, [], {'a': {'status': 'partial'}, 'b': {'status': 'complete'}})
    assert all(row['score'] is None for row in result.values())
    for row in result.values():
        coverage = row['coverage']
        assert coverage['inventory']['status'] == coverage['local']['status'] == 'complete'
        assert coverage['incoming']['status'] == coverage['cycles']['status'] == 'partial'
        assert row['folderMetrics']['coverage']['inventory']['status'] == 'complete'
    assert result['two/b.py']['coverage']['outgoing']['status'] == 'complete'
    summary = organization_summary(result)
    assert summary['coverage']['local'] == {'complete': 2, 'unknown': 0, 'total': 2}
    assert summary['coverage']['cycles'] == {'complete': 0, 'unknown': 2, 'total': 2}
    assert summary['score'] is None


def test_confirmed_cycle_remains_visible_on_partial_topology():
    """A proven cycle is a finding even though unrelated missing targets prevent full O."""
    nodes = [declaration('a', 'one/a.py'), declaration('b', 'two/b.py'), declaration('c', 'three/c.py')]
    edges = [{'source': 'a', 'target': 'b', 'relation': 'calls'}, {'source': 'b', 'target': 'a', 'relation': 'calls'}]
    deps = {node['id']: {'status': 'complete'} for node in nodes}
    deps['c']['status'] = 'partial'
    result = organization_profiles(nodes, edges, deps)
    assert 'organization-cycle' in rules(result['one/a.py'])
    assert result['one/a.py']['metrics']['inCycle']
    assert all(row['score'] is None for row in result.values())


def test_inferred_edge_never_proves_cycle_or_outgoing_count():
    """Explicit hypotheses remain unknown instead of becoming observed architecture facts."""
    nodes = [declaration('a', 'a.py'), declaration('b', 'b.py')]
    deps = {node['id']: {'status': 'complete'} for node in nodes}
    edges = [{'source': 'a', 'target': 'b', 'relation': 'calls', 'confidence': 'INFERRED'},
             {'source': 'b', 'target': 'a', 'relation': 'calls', 'confidence': 'EXTRACTED'}]
    result = organization_profiles(nodes, edges, deps)
    assert result['a.py']['metrics']['outgoingFiles'] == 0
    assert not result['a.py']['metrics']['inCycle']
    assert 'organization-cycle' not in rules(result['a.py'])
    assert result['a.py']['coverage']['outgoing']['status'] == 'partial'
    assert result['b.py']['coverage']['outgoing']['status'] == 'complete'


def test_missing_target_cannot_be_hidden_by_inconsistent_complete_dependencies():
    """Topology independently checks missing target paths, including targets outside selected scope."""
    result = profiles([declaration('a', 'a.py')], [('a', 'absent')])['a.py']
    assert result['score'] is None
    assert result['coverage']['outgoing']['status'] == 'partial'
    assert result['coverage']['local']['status'] == 'complete'


def test_known_external_target_is_a_boundary_not_a_missing_internal_file():
    """External APIs are not files and must not create phantom file links or unknown topology."""
    nodes = [declaration('a', 'a.py'), {'id': 'external', 'kind': 'external'}]
    result = profiles(nodes, [('a', 'external')])['a.py']
    assert result['score'] == 100
    assert result['metrics']['outgoingFiles'] == 0
    assert result['coverage']['cycles']['status'] == 'complete'


def test_outside_source_does_not_expand_selected_scope_denominator():
    """A target proxy may resolve a path without pretending its unselected outgoing edges were measured."""
    nodes = [declaration('a', 'a.py'), {'id': 'other', 'file': 'other.py'}]
    result = profiles(nodes, [('other', 'a')])['a.py']
    assert result['score'] == 100
    assert result['metrics']['incomingFiles'] == 0
    assert result['coverage']['incoming']['scope'] == 'selected-source-graph'


def test_legacy_summary_keeps_coverage_unknown_without_mutating_record():
    """Old numeric results remain readable, but missing per-dimension evidence is not reconstructed."""
    result = profiles([declaration('a', 'a.py')])
    del result['a.py']['coverage']
    before = deepcopy(result)
    summary = organization_summary(result)
    assert summary['coverage']['local'] == {'complete': 0, 'unknown': 1, 'total': 1}
    assert summary['score'] == 100
    assert result == before


def test_no_body_and_unavailable_file_keep_explicit_unknown_local_coverage():
    """Missing executable inventory cannot be replaced by a healthy empty-file score."""
    nodes = [declaration('interface', 'interface.cs'), declaration('missing', 'missing.cs')]
    nodes[0]['complexity']['reason'] = 'declaration has no body'
    nodes[1]['complexity']['status'] = 'unavailable'
    result = profiles(nodes, files={'interface.cs': {'status': 'complete'}, 'missing.cs': {'status': 'unavailable'}})
    assert all(row['score'] is None for row in result.values())
    assert result['interface.cs']['coverage']['inventory']['status'] == 'partial'
    assert result['missing.cs']['coverage']['local']['status'] == 'partial'


def test_external_boundary_never_creates_internal_file_even_with_display_path():
    """A library's presentation path does not make it part of the selected project topology."""
    nodes = [declaration('a', 'a.py'), {'id': 'external', 'kind': 'external', 'file': 'System/Library.cs'}]
    result = profiles(nodes, [('a', 'external')])['a.py']
    assert result['metrics']['outgoingFiles'] == 0
    assert result['score'] == 100


def test_snapshot_working_and_draft_preserve_available_local_facts(health_store):
    """Three consumers agree about the same unresolved call without hiding simple sibling metrics."""
    from atlas.code_health_source import parse_health_source
    state, _, _, _ = health_store
    content = b'def simple():\n    return 1\n\ndef unknown():\n    return missing.send()\n'
    methods = parse_health_source('sample.py', content)['methods']
    captured = enrich_code_health({'nodes': deepcopy(methods), 'edges': []})['nodes']
    service = CodeHealthService(state)
    working = service.evaluate_contents({'nodes': [], 'edges': []}, {'sample.py': content})['nodes']
    draft = service.evaluate_contents({'nodes': [], 'edges': []}, {'sample.py': content}, source_kind='draft')['nodes']
    reference = None
    for nodes in (captured, working, draft):
        health = next(node['health'] for node in nodes if node['label'].startswith('simple'))
        assert health['score'] is health['organizationScore'] is health['transparencyScore'] is None
        assert health['components']['flow'] == health['components']['size'] == 100
        assert health['readabilityScore'] == 100
        assert health['organization']['coverage']['local']['status'] == 'complete'
        coverage = health['organization']['coverage']
        if reference is None:
            reference = coverage
        assert coverage == reference


def test_partial_health_summary_never_labels_known_subset_as_full_mean():
    """Independent components expose coverage, while an incomplete total retains UNKNOWN."""
    from atlas.code_health_comparison import summarize
    nodes = [{'id': 'known', 'health': {'status': 'complete', 'score': 100, 'version': 'atlas-health-v3'}},
             {'id': 'unknown', 'health': {'status': 'partial', 'score': None}}]
    summary = summarize(nodes)
    assert summary['meanHealth'] is None
    assert summary['knownMeanHealth'] == 100
    assert summary['assessedCount'] == 1 and summary['unknownCount'] == 1
    assert summary['componentCoverage']['flow'] == {'complete': 0, 'unknown': 2, 'total': 2}


def test_unrelated_unsupported_file_keeps_proven_file_and_folder_inventory():
    """Explicit full-file parsing proves local facts even when another selected parser fails."""
    nodes = [declaration('a', 'same/a.py', 7)]
    result = profiles(nodes, files={'same/a.py': {'status': 'complete', 'truncated': False},
                                    'same/b.unsupported': {'status': 'unavailable'}}, complete=False)
    known = result['same/a.py']
    assert known['coverage']['inventory']['status'] == known['coverage']['local']['status'] == 'complete'
    assert known['metrics']['statements'] == 7
    assert known['folderMetrics']['files'] == 2
    assert known['folderMetrics']['coverage']['inventory']['status'] == 'complete'
    assert known['coverage']['cycles']['status'] == 'partial'
    assert known['score'] is None
    assert result['same/b.unsupported']['coverage']['local']['status'] == 'partial'


def test_captured_global_truncation_cannot_invent_full_local_inventory():
    """Without per-file parser evidence, a globally truncated captured map stays incomplete."""
    result = profiles([declaration('a', 'a.py', 7)], complete=False)['a.py']
    assert result['coverage']['inventory']['status'] == result['coverage']['local']['status'] == 'partial'


@pytest.mark.parametrize('self_call', [False, True])
@pytest.mark.parametrize('hash_location', ['sourceHash', 'evidence'])
def test_stale_source_edges_do_not_create_observed_topology(self_call, hash_location):
    """A stale edge stays uncertain even if an old dependency summary claims complete."""
    nodes = [declaration('a', 'left/a.py'), declaration('b', 'right/b.py')]
    for node in nodes:
        node['complexity']['sourceHash'] = 'current'
    pairs = [('a', 'a')] if self_call else [('a', 'b'), ('b', 'a')]
    stale = {'sourceHash': 'stale'} if hash_location == 'sourceHash' else {'evidence': {'sourceHash': 'stale'}}
    edges = [dict(source=source, target=target, relation='calls', **stale) for source, target in pairs]
    dependencies = {node['id']: {'status': 'complete'} for node in nodes}
    result = organization_profiles(nodes, edges, dependencies)
    for row in result.values():
        assert row['metrics']['outgoingFiles'] == row['metrics']['incomingFiles'] == 0
        assert row['metrics']['inCycle'] is False
        assert row['folderMetrics']['inCycle'] is False
        assert 'organization-cycle' not in rules(row)
        assert row['coverage']['cycles']['status'] == 'partial'
        assert row['score'] is None
    assert result['left/a.py']['coverage']['outgoing']['status'] == 'partial'


def test_source_matched_cycle_remains_known_on_otherwise_partial_graph():
    """The shared hash fence preserves a real static cycle while rejecting stale alternatives."""
    nodes = [declaration('a', 'left/a.py'), declaration('b', 'right/b.py')]
    for node in nodes:
        node['complexity']['sourceHash'] = 'current'
    edges = [{'source': 'a', 'target': 'b', 'relation': 'calls', 'evidence': {'sourceHash': 'current'}},
             {'source': 'b', 'target': 'a', 'relation': 'calls', 'evidence': {'sourceHash': 'current'}},
             {'source': 'a', 'target': 'missing', 'relation': 'calls', 'evidence': {'sourceHash': 'stale'}}]
    dependencies = {node['id']: {'status': 'partial'} for node in nodes}
    result = organization_profiles(nodes, edges, dependencies)
    assert all(row['metrics']['inCycle'] for row in result.values())
    assert all('organization-cycle' in rules(row) for row in result.values())
    assert all(row['score'] is None for row in result.values())
