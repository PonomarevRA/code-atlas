"""Real normalized graph/store projections keep semantic evidence separate from immutable input."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
import gc
import weakref

import pytest

from atlas import store
from atlas.mod_analysis import active_semantic_rules
from atlas.semantic_models import (SemanticContext, SemanticCoverage, SemanticFact,
                                   SemanticResult, SemanticSymbol, SourceSpan)
from atlas.semantic_overlay import SemanticOverlayReader, make_overlay, write_overlay
from atlas.semantic_sources import declaration_bindings, DeclarationIndex, span_bytes
from test_grouping_cache import legacy_store


@pytest.fixture
def semantic_project(tmp_path):
    """Use actual Python AST spans and a selected declarative mod, with no external engine required."""
    root = tmp_path / 'source'
    root.mkdir()
    a = b'from b import work\n\ndef run():\n    return work()\n'
    b = b'def work():\n    return 1\n'
    (root / 'a.py').write_bytes(a)
    (root / 'b.py').write_bytes(b)
    state, project_id, path, _ = legacy_store(tmp_path)
    hashes = {'a.py': sha256(a).hexdigest(), 'b.py': sha256(b).hexdigest()}
    nodes = []
    for identifier, filename, line in [('run', 'a.py', 3), ('work', 'b.py', 1)]:
        nodes.append({'id': identifier, 'label': identifier + '()', 'kind': 'method',
                      'file': filename, 'line': line,
                      'memberFacts': {'name': identifier, 'language': 'python'},
                      'commentSource': {'sourceKind': 'snapshot-captured-source', 'sourceHash': hashes[filename]}})
    graph = {'nodes': nodes, 'edges': [], 'metadata': {'snapshotId': path.parent.name}}
    store.write_json(path, graph)
    state.projects[project_id]['path'] = str(root)
    package = state.mods.directory / 'semantic-python'
    package.mkdir(parents=True)
    (package / 'mod.json').write_text(json.dumps({'apiVersion': 1, 'id': 'semantic-python',
        'name': 'Python semantics', 'version': '1', 'autoActivate': {'extensions': ['.py'], 'manifestNames': []},
        'analysis': {'apiVersion': 1, 'semanticProviders': [
            {'id': 'jedi', 'language': 'python', 'strategy': 'jedi-local-v1'}]}}))
    rules = active_semantic_rules(state.mods, inventory={'files': list(hashes), 'truncated': False})
    fingerprint = rules['semanticProviders'][0]['modFingerprint']
    context = SemanticContext(path.parent.name, project_id, str(root), 'python', 'a' * 64, hashes, fingerprint)
    source = SemanticSymbol('engine-run', 'method', 'run', SourceSpan('a.py', 2, 0, 3, 17))
    target = SemanticSymbol('engine-work', 'method', 'work', SourceSpan('b.py', 0, 4, 0, 8))
    fact = SemanticFact('call', source, target, 'confirmed-static', SourceSpan('a.py', 3, 11, 3, 17))
    result = SemanticResult(context, 'semantic-python/jedi', fingerprint, 'b' * 64,
                            facts=(fact,), coverage={'definition': SemanticCoverage('partial', 1, 1)})
    contents, bindings = declaration_bindings(root, graph, hashes, path.parent.name, hashes)
    try:
        yield state, project_id, path, root, graph, result, rules, contents, bindings
    finally:
        state.close()


def test_overlay_source_mapping_preserves_raw_bytes_and_shared_consumers(semantic_project):
    """HTTP graph, pinned node/neighbors, health scope and Graphify MCP use one effective revision."""
    state, project_id, path, root, graph, result, rules, contents, bindings = semantic_project
    original = path.read_bytes()
    raw = state.raw_graph(project_id).read_bytes()
    delta = make_overlay(path, graph, root, [result], bindings, contents, rules)
    write_overlay(path, delta)
    projected = state.get_graph(project_id)
    assert {n['id'] for n in projected['nodes']} == {'run', 'work'}
    assert any(e['source'] == 'run' and e['target'] == 'work' for e in projected['edges'])
    node = state.source_snapshot(project_id, 'run', path.parent.name)[1]
    assert node['semanticFacts'][0]['runtimeSelectionKnown'] is False
    health = state.health_snapshot(project_id, path.parent.name)[1]
    assert any(edge['target'] == 'work' for edge in health['edges'])
    _, comparison = state.comparison_snapshot(project_id)
    assert comparison['metadata']['semanticEnrichment']['revision'] == delta['revision']
    mcp = json.loads(state.published_mcp_graph(project_id).read_text())
    assert mcp['graph']['metadata']['semanticEnrichment']['revision'] == delta['revision']
    assert mcp['links'][0]['runtimeSelectionKnown'] is False
    assert path.read_bytes() == original and state.raw_graph(project_id).read_bytes() == raw
    assert graph['edges'] == [] and 'semanticFacts' not in graph['nodes'][0]


def test_off_and_changed_snapshot_restore_exact_baseline(semantic_project):
    """Disabling a semantic-only mod removes the delta without rebuilding Graphify."""
    state, project_id, path, root, graph, result, rules, contents, bindings = semantic_project
    delta = make_overlay(path, graph, root, [result], bindings, contents, rules)
    write_overlay(path, delta)
    assert state._published_snapshot(project_id, path)['edges']
    item = next(m for m in state.mods.catalog()['mods'] if m['id'] == 'semantic-python')
    state.mods.enable(item['id'], item['fingerprint'], False)
    restored = state._published_snapshot(project_id, path)
    assert restored['edges'] == [] and 'semanticEnrichment' not in restored['metadata']
    assert state.published_mcp_graph(project_id) == state.raw_graph(project_id)


def test_ambiguous_equal_spans_and_same_names_never_bind(semantic_project):
    """A duplicated raw declaration and a location-free symbol both remain unresolved references."""
    state, _, path, root, graph, result, rules, contents, bindings = semantic_project
    other = {**graph['nodes'][1], 'id': 'duplicate'}
    graph['nodes'].append(other)
    duplicate = {**next(b for b in bindings if b['nodeId'] == 'work'), 'nodeId': 'duplicate'}
    index = DeclarationIndex([*bindings, duplicate], contents)
    assert index.resolve(result.facts[0].target) is None
    assert index.resolve(SemanticSymbol('id', 'method', 'work', graph_node_id='work')) is None
    delta = make_overlay(path, graph, root, [result], [*bindings, duplicate], contents, rules)
    assert delta['edges'] == []
    assert delta['facts']['run'][0]['target']['graph_node_id'] is None


@pytest.mark.parametrize('kind', ['variable', 'class', 'parameter'])
def test_reference_inside_method_does_not_identify_containing_method(semantic_project, kind):
    """A source-anchored local definition is retained as a symbol, never mapped to its owner method."""
    _, _, path, root, graph, result, rules, _, _ = semantic_project
    content = b'def run():\n    value = 1\n    return value\n'
    (root / 'a.py').write_bytes(content)
    digest = sha256(content).hexdigest()
    graph['nodes'][0].update(line=1, commentSource={'sourceKind': 'snapshot-captured-source', 'sourceHash': digest})
    store.write_json(path, graph)
    context = replace(result.context, source_hashes={**result.context.source_hashes, 'a.py': digest})
    source = SemanticSymbol('run', 'function', 'run', SourceSpan('a.py', 0, 0, 2, 16))
    target = SemanticSymbol('local', kind, 'value', SourceSpan('a.py', 1, 4, 1, 9))
    reference = SemanticFact('reference', source, target, 'confirmed-static', SourceSpan('a.py', 2, 11, 2, 16))
    contents, bindings = declaration_bindings(root, graph, context.source_hashes, path.parent.name, context.source_hashes)
    delta = make_overlay(path, graph, root, [replace(result, context=context, facts=(reference,))], bindings, contents, rules)
    assert delta['edges'] == []
    assert delta['facts']['run'][0]['target']['graph_node_id'] is None
    assert delta['facts']['run'][0]['target']['label'] == 'value'


def test_dedup_and_conflict_keep_original_evidence(semantic_project):
    """Exact callsite duplicates merge, while incompatible raw targets remain visible as conflicts."""
    _, _, path, root, graph, result, rules, contents, bindings = semantic_project
    start, end = span_bytes(result.facts[0].callsite, contents['a.py'])
    graph['edges'].append({'source': 'run', 'target': 'run', 'relation': 'calls',
                           'callSite': {'startByte': start, 'endByte': end}, 'confidence': 'INFERRED'})
    delta = make_overlay(path, graph, root, [replace(result, facts=result.facts * 2)], bindings, contents, rules)
    assert len(delta['edges']) == 1 and len(delta['edges'][0]['semanticEvidence']) == 2
    assert delta['conflicts'][0]['graphifyTarget'] == 'run'
    assert graph['edges'][0]['confidence'] == 'INFERRED'


def test_duplicate_raw_edge_keeps_added_semantic_evidence(semantic_project):
    """Deduplication must retain compiler evidence without changing the original raw edge."""
    state, _, path, root, graph, result, rules, contents, bindings = semantic_project
    left, right = span_bytes(result.facts[0].callsite, contents['a.py'])
    raw = {'source': 'run', 'target': 'work', 'relation': 'calls',
           'confidence': 'EXTRACTED', 'callSite': {'startByte': left, 'endByte': right}}
    graph['edges'].append(raw)
    write_overlay(path, make_overlay(path, graph, root, [result], bindings, contents, rules))
    projected = SemanticOverlayReader().project(graph, path, state.mods)
    assert len(projected['edges']) == 1
    assert projected['edges'][0]['semanticEvidence'][0]['runtimeSelectionKnown'] is False
    assert 'semanticEvidence' not in raw


@pytest.mark.parametrize('mutation', ['snapshot', 'source', 'missing-ids', 'bad-json', 'oversized'])
def test_invalid_sidecars_leave_baseline_usable(semantic_project, mutation):
    """Malformed, obsolete or unrelated optional data never damages the source map."""
    state, _, path, root, graph, result, rules, contents, bindings = semantic_project
    delta = make_overlay(path, graph, root, [result], bindings, contents, rules)
    if mutation == 'snapshot':
        delta['snapshotId'] = 'another'
    elif mutation == 'source':
        delta['sourceHash'] = 'c' * 64
    elif mutation == 'missing-ids':
        delta['edges'][0]['target'] = 'not-in-graph'
    write_overlay(path, delta)
    sidecar = path.with_name('semantic-overlay.json')
    if mutation == 'bad-json':
        sidecar.write_text('{')
    elif mutation == 'oversized':
        sidecar.write_bytes(b'x' * (8 * 1024 * 1024 + 1))
    assert SemanticOverlayReader().project(graph, path, state.mods) is graph


def test_unicode_coordinate_units_and_split_surrogates():
    """UTF16 positions resolve the same byte span; half a surrogate pair is rejected."""
    content = 'a😀é\n'.encode()
    assert span_bytes(SourceSpan('a.py', 0, 1, 0, 3), content) == (1, 5)
    assert span_bytes(SourceSpan('a.py', 0, 1, 0, 5, 'utf-8'), content) == (1, 5)
    with pytest.raises(UnicodeDecodeError):
        span_bytes(SourceSpan('a.py', 0, 2, 0, 3), content)
    assert span_bytes(SourceSpan('a.py', 1, 0, 1, 1), 'a\u2028b\nq'.encode()) == (6, 7)


def test_changed_source_is_not_usable_for_declaration_binding(semantic_project):
    """Current files must still match the captured source hash before cold publication."""
    _, _, path, root, graph, result, _, _, _ = semantic_project
    (root / 'a.py').write_text('def run():\n    return 2\n')
    contents, bindings = declaration_bindings(root, graph, ['a.py'], path.parent.name, result.context.source_hashes)
    assert contents == {} and bindings == []


@pytest.mark.parametrize('digest', [None, 'c' * 64])
def test_target_source_must_be_pinned_by_the_engine_result(semantic_project, digest):
    """A declaration's real graph span cannot mask absent or contradictory engine target provenance."""
    _, _, path, root, graph, result, rules, contents, bindings = semantic_project
    hashes = dict(result.context.source_hashes)
    if digest is None:
        hashes.pop('b.py')
    else:
        hashes['b.py'] = digest
    invalid = replace(result, context=replace(result.context, source_hashes=hashes))
    with pytest.raises(ValueError, match='target source is not pinned'):
        make_overlay(path, graph, root, [invalid], bindings, contents, rules)


def test_projection_cache_retains_exact_base_identity(semantic_project):
    """Recycled Python object IDs must never reuse a different grouping response's projection."""
    state, _, path, root, graph, result, rules, contents, bindings = semantic_project
    write_overlay(path, make_overlay(path, graph, root, [result], bindings, contents, rules))

    class OwnedGraph(dict):
        """A weak-referenceable owned response makes base lifetime observable."""

    reader = SemanticOverlayReader()
    candidate = OwnedGraph(deepcopy(graph))
    handle = weakref.ref(candidate)
    projected = reader.project(candidate, path, state.mods)
    del candidate
    gc.collect()
    assert handle() is not None
    for index in range(100):
        candidate = OwnedGraph(deepcopy(graph))
        candidate['nodes'][0]['community'] = str(index)
        projected = reader.project(candidate, path, state.mods)
        assert projected['nodes'][0]['community'] == str(index)
    assert len(reader.cache) <= 2
    reader.cache.clear()
    gc.collect()
    assert handle() is None


def test_bounded_semantic_targets_never_shorten_raw_identity():
    """Snake-case engine DTO identities follow the same no-shortening rule as graph IDs."""
    from atlas.analysis_facts import bounded_fact
    from atlas.graph_context import _bounded_value
    identifier = 'raw-' + 'x' * 500
    value = [{'target': {'graph_node_id': identifier, 'symbol_id': 'semantic:' + 'a' * 32}}]
    assert bounded_fact(value)[0]['target']['graph_node_id'] == identifier
    assert _bounded_value(value, chars=160)[0][0]['target']['graph_node_id'] == identifier


def test_validated_delta_reuses_fresh_layouts_without_catalog_reads(semantic_project, monkeypatch):
    """Re-decoded grouping layouts reuse validation while keeping owned metadata isolated."""
    from atlas import semantic_overlay
    state, _, path, root, graph, result, rules, contents, bindings = semantic_project
    delta = make_overlay(path, graph, root, [result], bindings, contents, rules)
    write_overlay(path, delta)
    reader = SemanticOverlayReader()
    first = reader.project(deepcopy(graph), path, state.mods)
    first['metadata']['semanticEnrichment']['scopes'][0]['status'] = 'tampered'
    first['metadata']['semanticEnrichment']['conflicts'].append({'tampered': True})
    def forbidden(*args, **kwargs):
        raise AssertionError('Unchanged layout must reuse verified delta')
    monkeypatch.setattr(semantic_overlay, 'active_semantic_rules', forbidden)
    monkeypatch.setattr(semantic_overlay, '_validate_overlay', forbidden)
    second = reader.project(deepcopy(graph), path, state.mods)
    assert second['edges'] and second['metadata']['semanticEnrichment']['conflicts'] == []
    assert second['metadata']['semanticEnrichment']['scopes'][0]['status'] == result.status
    missing = deepcopy(graph)
    missing['nodes'] = [missing['nodes'][0], {**missing['nodes'][1], 'id': 'another-layout-id'}]
    assert reader.project(missing, path, state.mods) is missing
    assert len(reader.deltas) == 1


def test_delta_cache_revokes_changed_pinned_package_asset(semantic_project):
    """An asset edit invalidates explicit package activation despite an unchanged manifest."""
    state, _, path, root, graph, result, rules, contents, bindings = semantic_project
    package = state.mods.directory / 'semantic-python'
    (package / 'mod.json').write_bytes((state.mods.bundled / 'semantic-python' / 'mod.json').read_bytes())
    state.mods.bundled = package.parent / 'absent-bundled'
    asset = package / 'notes.txt'
    asset.write_text('one')
    item = next(item for item in state.mods.catalog()['mods'] if item['id'] == 'semantic-python')
    state.mods.enable(item['id'], item['fingerprint'], True)
    rules = active_semantic_rules(state.mods, inventory={'files': ['a.py', 'b.py'], 'truncated': False})
    write_overlay(path, make_overlay(path, graph, root, [result], bindings, contents, rules))
    reader = SemanticOverlayReader()
    assert reader.project(deepcopy(graph), path, state.mods)['edges']
    asset.write_text('two changed')
    candidate = deepcopy(graph)
    assert reader.project(candidate, path, state.mods) is candidate
    assert len(reader.deltas) <= 2


def test_delta_cache_invalidates_snapshot_and_bounds_retention(semantic_project):
    """Snapshot changes revoke earlier derivatives, and repeated edits retain only two deltas."""
    state, _, path, root, graph, result, rules, contents, bindings = semantic_project
    write_overlay(path, make_overlay(path, graph, root, [result], bindings, contents, rules))
    reader = SemanticOverlayReader()
    assert reader.project(deepcopy(graph), path, state.mods)['edges']
    for index in range(5):
        path.write_text(json.dumps({**graph, 'layoutRevision': index}))
        candidate = deepcopy(graph)
        assert reader.project(candidate, path, state.mods) is candidate
        assert len(reader.deltas) <= 2
