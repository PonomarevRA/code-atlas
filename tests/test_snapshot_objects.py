"""
Storage contracts for immutable graph chunks, legacy readers and hostile manifests.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path

import pytest

from atlas.snapshot_object_store import canonical_bytes, read_object, write_object, checked_object_path
from atlas.snapshot_objects import MARKER, object_root, read_snapshot, snapshot_source_stamp, write_snapshot


def graph():
    """
    Exercise interleaved owners, duplicate edges, external endpoints and arbitrary metadata.
    """
    return {'nodes': [
        {'id': 'a', 'file': 'a.py', 'label': 'А()', 'codeProjectFacts': {'project': 'one'}},
        {'id': 'b', 'file': 'b.py', 'codeProjectFacts': {'project': 'one'}},
        {'id': 'c', 'file': 'a.py', 'codeProjectFacts': None},
        {'id': 'd', 'label': 'external'}],
        'edges': [{'source': 'a', 'target': 'b', 'relation': 'calls', 'line': 5},
                  {'source': 'b', 'target': 'c', 'relation': 'calls'},
                  {'source': 'a', 'target': 'b', 'relation': 'calls', 'line': 5},
                  {'source': 'a', 'target': 'outside', 'relation': 'references'}],
        'metadata': {'snapshotId': 'first', 'arbitrary': {'unicode': 'Привет'}},
        'unknownTopLevel': ['preserved', 42]}


def path_for(tmp_path, name='first'):
    """
    Give snapshots a common project root, as the production layout does.
    """
    return tmp_path / 'project' / name / 'atlas-graph.json'


def document(path):
    """
    Read the small persisted manifest for deliberate format-level negative tests.
    """
    return json.loads(path.read_text())


def test_roundtrip_preserves_order_parallel_edges_metadata_and_input(tmp_path):
    """
    Chunk ownership and endpoint interning must not alter observable graph facts.
    """
    path = path_for(tmp_path)
    original = graph()
    before = deepcopy(original)
    write_snapshot(path, original)
    assert original == before
    assert read_snapshot(path) == before
    restored = read_snapshot(path)
    restored['nodes'][0]['codeProjectFacts']['project'] = 'changed'
    assert restored['nodes'][1]['codeProjectFacts']['project'] == 'one'
    assert MARKER in document(path)
    assert 'nodes' not in document(path)


def test_new_snapshot_reuses_unchanged_file_chunks(tmp_path):
    """
    A one-file edit writes new local objects without replacing retained snapshot bytes.
    """
    first, second = path_for(tmp_path), path_for(tmp_path, 'second')
    old = graph()
    one = write_snapshot(first, old)
    paths = list(object_root(first).rglob('*.json.gz'))
    stamps = {p: p.stat().st_mtime_ns for p in paths}
    changed = deepcopy(old)
    changed['metadata']['snapshotId'] = 'second'
    changed['nodes'][1]['label'] = 'changed only b.py'
    two = write_snapshot(second, changed)
    refs1 = {r['hash'] for r in one['nodes']['chunks']}
    refs2 = {r['hash'] for r in two['nodes']['chunks']}
    assert len(refs1 & refs2) == len(refs1) - 1
    assert all(p.stat().st_mtime_ns == stamp for p, stamp in stamps.items())
    assert read_snapshot(first) == old
    assert read_snapshot(second) == changed


def test_identical_snapshots_write_no_additional_objects(tmp_path):
    """
    Multiple manifests can refer to the same immutable compressed objects.
    """
    one, two = path_for(tmp_path), path_for(tmp_path, 'second')
    write_snapshot(one, graph())
    names = sorted(object_root(one).rglob('*.json.gz'))
    write_snapshot(two, graph())
    assert sorted(object_root(one).rglob('*.json.gz')) == names
    assert snapshot_source_stamp(one)[1] == len(canonical_bytes(graph()))


def test_legacy_json_and_unknown_top_level_data_are_readable(tmp_path):
    """
    Format dispatch retains existing uncompressed snapshots and their original stat size.
    """
    path = path_for(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(graph()))
    assert read_snapshot(path) == graph()
    assert snapshot_source_stamp(path) == [path.stat().st_mtime_ns, path.stat().st_size]


def test_concurrent_identical_object_writers_are_no_clobber(tmp_path):
    """
    Atomic no-replace publication gives all writers the same verified object.
    """
    root = tmp_path / '_objects'
    value = {'records': ['repeated'] * 500}
    with ThreadPoolExecutor(max_workers=8) as workers:
        refs = list(workers.map(lambda _: write_object(root, value), range(24)))
    assert all(ref == refs[0] for ref in refs)
    assert len(list(root.rglob('*.json.gz'))) == 1
    assert not list(root.rglob('*.tmp'))
    assert read_object(root, refs[0]) == value


def test_existing_corrupt_object_is_not_silently_overwritten(tmp_path):
    """
    A damaged prior object is evidence of corruption, not an ordinary cache miss.
    """
    root = tmp_path / '_objects'
    ref = write_object(root, {'value': 'original'})
    path = checked_object_path(root, ref['hash'])
    path.write_bytes(b'broken')
    with pytest.raises(ValueError):
        write_object(root, {'value': 'original'})
    assert path.read_bytes() == b'broken'


def test_object_trailing_data_and_expanded_length_are_rejected(tmp_path):
    """
    Neither concatenated gzip streams nor falsified expanded lengths are accepted.
    """
    root = tmp_path / '_objects'
    ref = write_object(root, {'value': 'original'})
    with pytest.raises(ValueError):
        read_object(root, {**ref, 'bytes': ref['bytes'] - 1})
    path = checked_object_path(root, ref['hash'])
    path.write_bytes(path.read_bytes() + b'trailing')
    with pytest.raises(ValueError):
        read_object(root, ref)


@pytest.mark.parametrize('change', ['version', 'missing', 'duplicate-order', 'wrong-count', 'negative-index', 'traversal', 'header-null', 'wrong-length'])
def test_malformed_manifest_fails_closed(tmp_path, change):
    """
    Invalid references and record coordinates must never silently return a partial map.
    """
    path = path_for(tmp_path)
    write_snapshot(path, graph())
    value = document(path)
    m = value[MARKER]
    if change == 'version':
        m['version'] = 'unknown'
    elif change == 'missing':
        checked_object_path(object_root(path), m['header']['hash']).unlink()
    elif change == 'duplicate-order':
        m['nodes']['order'][1] = m['nodes']['order'][0]
    elif change == 'wrong-count':
        m['edges']['count'] += 1
    elif change == 'negative-index':
        m['nodes']['order'][0] = [-1, 0]
    elif change == 'traversal':
        m['header']['hash'] = '../outside'
    elif change == 'header-null':
        m['header'] = None
    else:
        m['logicalBytes'] += 1
    path.write_text(json.dumps(value))
    with pytest.raises((OSError, ValueError)):
        read_snapshot(path)


def test_symlink_object_root_and_object_are_rejected(tmp_path):
    """
    A manifest cannot redirect object reads or writes outside the managed project store.
    """
    path = path_for(tmp_path)
    write_snapshot(path, graph())
    m = document(path)[MARKER]
    obj = checked_object_path(object_root(path), m['header']['hash'])
    content = obj.read_bytes()
    outside = tmp_path / 'outside.gz'
    outside.write_bytes(content)
    obj.unlink()
    obj.symlink_to(outside)
    with pytest.raises(ValueError):
        read_snapshot(path)
    with pytest.raises(ValueError):
        write_snapshot(path, graph())


def test_source_stamp_tracks_referenced_object_revisions_and_missing_objects(tmp_path):
    """
    A tiny unchanged manifest must not keep a stale warm graph after object replacement.
    """
    path = path_for(tmp_path)
    write_snapshot(path, graph())
    first = snapshot_source_stamp(path)
    m = document(path)[MARKER]
    obj = checked_object_path(object_root(path), m['header']['hash'])
    obj.write_bytes(obj.read_bytes() + b'corruption')
    assert snapshot_source_stamp(path) != first
    with pytest.raises(ValueError):
        read_snapshot(path)
    obj.unlink()
    with pytest.raises(OSError):
        snapshot_source_stamp(path)


def test_failed_manifest_publication_keeps_previous_snapshot(tmp_path, monkeypatch):
    """
    Writing some new immutable objects must not publish a partial snapshot.
    """
    import atlas.snapshot_objects as module
    path = path_for(tmp_path)
    write_snapshot(path, graph())
    before = path.read_bytes()
    monkeypatch.setattr(module, 'MAX_MANIFEST_BYTES', 1)
    changed = graph()
    changed['nodes'][0]['label'] = 'new'
    with pytest.raises(ValueError):
        write_snapshot(path, changed)
    assert path.read_bytes() == before


def test_empty_graph_and_large_file_chunking(tmp_path):
    """
    Empty graphs and owners larger than one record chunk remain lossless.
    """
    empty = {'nodes': [], 'edges': [], 'metadata': {}}
    one = path_for(tmp_path)
    write_snapshot(one, empty)
    assert read_snapshot(one) == empty
    large = {'nodes': [{'id': str(i), 'file': 'one.py'} for i in range(700)], 'edges': [], 'metadata': {}}
    two = path_for(tmp_path, 'second')
    m = write_snapshot(two, large)
    assert len(m['nodes']['chunks']) == 3
    assert read_snapshot(two) == large


def test_volatile_health_timestamps_do_not_rewrite_node_objects(tmp_path):
    """
    A real rebuild timestamp changes its tiny table while preserving stable node chunks.
    """
    value = graph()
    value['nodes'][0]['health'] = {'score': 10, 'measuredAt': '2026-10-28T10:00:00Z'}
    value['nodes'][1]['health'] = {'score': 20, 'measuredAt': '2026-10-28T10:00:00Z'}
    value['nodes'][2]['health'] = {'score': 30, 'measuredAt': None}
    value['nodes'][3]['health'] = {'score': 40}
    first = write_snapshot(path_for(tmp_path), value)
    changed = deepcopy(value)
    for node in changed['nodes'][:2]:
        node['health']['measuredAt'] = '2026-10-28T11:00:00Z'
    second_path = path_for(tmp_path, 'second')
    second = write_snapshot(second_path, changed)
    assert first['nodes'] == second['nodes']
    assert first['edges'] == second['edges']
    assert first['healthTimes'] != second['healthTimes']
    assert read_snapshot(second_path) == changed


def test_actual_store_rebuild_interns_measured_health_times(tmp_path, monkeypatch):
    """
    The real enrichment/publication path reuses nodes across unchanged-source rebuilds.
    """
    from atlas import store
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'a.py').write_text('def run():\n    return 42\n')

    def provider(project, output):
        """
        Keep extraction deterministic while the actual Atlas enrichment runs normally.
        """
        raw = output / 'graphify-out' / 'graph.json'
        raw.parent.mkdir(parents=True)
        raw.write_text('{}')
        return {'nodes': [{'id': 'a_run', 'label': 'run()', 'kind': 'method', 'file': 'a.py', 'line': 1}],
                'edges': [], 'metadata': {'rawGraphPath': str(raw)}}

    monkeypatch.setattr(store, 'build_graph', provider)
    state = store.ProjectStore(tmp_path / 'state')
    try:
        first_job = state.add(str(root))
        project_id = first_job['project']['id']
        state.executor.submit(lambda: None).result(timeout=30)
        first_path = Path(state.projects[project_id]['_graph'])
        first = document(first_path)[MARKER]
        graph1 = state._read_graph(first_path)
        assert any('measuredAt' in node.get('health', {}) for node in graph1['nodes'])
        second_job = state.rebuild(project_id)
        state.executor.submit(lambda: None).result(timeout=30)
        assert state.jobs[second_job['job']['id']]['status'] == 'completed'
        second_path = Path(state.projects[project_id]['_graph'])
        second = document(second_path)[MARKER]
        assert first['nodes'] == second['nodes']
        assert first['healthTimes'] != second['healthTimes']
        graph2 = state._read_graph(second_path)
        assert graph1['nodes'][0]['health']['measuredAt'] != graph2['nodes'][0]['health']['measuredAt']
    finally:
        state.close()


def test_same_digest_different_content_is_a_collision_error(tmp_path, monkeypatch):
    """
    Even a forced digest collision must never replace an already referenced object.
    """
    import atlas.snapshot_object_store as module

    class FixedDigest:
        """
        Force an equal-size collision without relying on a real SHA-256 collision.
        """
        def hexdigest(self):
            return 'c' * 64

    monkeypatch.setattr(module, 'sha256', lambda _: FixedDigest())
    root = tmp_path / '_objects'
    first = write_object(root, {'a': 'x'})
    with pytest.raises(ValueError, match='collision'):
        write_object(root, {'b': 'y'})
    assert read_object(root, first) == {'a': 'x'}


def test_source_node_snapshot_copies_only_selected_facts_and_provenance(tmp_path):
    """
    Source pages must not clone or leak the full graph while keeping exact revision fencing.
    """
    from atlas.store import StoreError
    from test_grouping_cache import legacy_store
    state, project_id, path, _ = legacy_store(tmp_path)
    try:
        node_id = state._read_graph(path)['nodes'][0]['id']
        root, subset = state.source_node_snapshot(project_id, node_id, path.parent.name)
        assert len(subset['nodes']) == 1 and subset['edges'] == []
        assert subset['metadata']['snapshotId'] == path.parent.name
        assert isinstance(subset['metadata']['semanticRevision'], str)
        subset['nodes'][0]['label'] = 'response mutation'
        assert state.source_node_snapshot(project_id, node_id, path.parent.name)[1]['nodes'][0]['label'] != 'response mutation'
        with pytest.raises(StoreError) as changed:
            state.source_node_snapshot(project_id, node_id, 'wrong-snapshot')
        assert changed.value.status == 409
        with pytest.raises(StoreError) as missing:
            state.source_node_snapshot(project_id, 'missing', path.parent.name)
        assert missing.value.status == 404
    finally:
        state.close()


def test_health_refresh_budget_uses_logical_manifest_size_before_hydration(tmp_path, monkeypatch):
    """
    A small manifest must not bypass the existing 256 MiB captured-health work budget.
    """
    from atlas.code_health_refresh import CodeHealthRefresh
    import atlas.snapshot_objects as objects
    from test_grouping_cache import legacy_store
    state, project_id, path, _ = legacy_store(tmp_path)
    worker = CodeHealthRefresh(state)
    monkeypatch.setattr(objects, 'snapshot_source_stamp', lambda _: [1, 257 * 1024 * 1024])

    def unexpected_read(_):
        """
        The oversized logical graph must be rejected before any full graph allocation.
        """
        pytest.fail('logical budget checked after graph hydration')

    monkeypatch.setattr(state, '_read_graph', unexpected_read)
    try:
        job = worker.request(project_id, path.parent.name)
        worker.executor.submit(lambda: None).result(timeout=10)
        assert worker.jobs[job['id']]['status'] == 'failed'
        assert '256 MiB' in worker.jobs[job['id']]['error']
    finally:
        worker.close()
        state.close()


def test_semantic_projection_retention_uses_logical_graph_size(tmp_path, monkeypatch):
    """
    A compact manifest must not retain a full projection above the previous 64 MiB cap.
    """
    import atlas.semantic_overlay as module
    path = path_for(tmp_path)
    write_snapshot(path, graph())
    reader = module.SemanticOverlayReader()
    monkeypatch.setattr(module, 'semantic_stamp', lambda *_: (1, 1))
    monkeypatch.setattr(module, 'snapshot_source_stamp', lambda _: [1, 65 * 1024 * 1024])
    monkeypatch.setattr(reader, '_project', lambda value, *_: value)
    value = graph()
    assert reader.project(value, path, None) is value
    assert not reader.cache


@pytest.mark.parametrize('declared', [1, 5000])
def test_interned_endpoint_expansion_stops_before_full_hydration(tmp_path, monkeypatch, declared):
    """
    Small endpoint tables must not trigger unbounded repeated-string expansion on read.
    """
    import atlas.snapshot_objects as module
    source, target = 's' * 500, 't' * 500
    value = {'nodes': [{'id': source}, {'id': target}],
             'edges': [{'source': source, 'target': target, 'line': index} for index in range(100)],
             'metadata': {}}
    path = path_for(tmp_path)
    write_snapshot(path, value)
    content = document(path)
    content[MARKER]['logicalBytes'] = declared
    path.write_text(json.dumps(content))
    monkeypatch.setattr(module, 'MAX_EXPANDED_BYTES', 5000)
    calls = []
    original = module._json_size

    def counted(record):
        """
        Observe record counting without serializing or retaining expanded edge strings.
        """
        calls.append(record)
        return original(record)

    monkeypatch.setattr(module, '_json_size', counted)
    with pytest.raises(ValueError, match='exceeds budget'):
        read_snapshot(path)
    assert len(calls) < 30
    assert not any(isinstance(row, dict) and 'source' in row and 'target' in row for row in calls)
