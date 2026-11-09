"""Lossless persisted health profiles and raw-relative deltas keep response facts isolated."""

from copy import deepcopy
import json

import pytest

from atlas.health_payload_codec import MARKER, decode_health_payloads, encode_health_payloads
from atlas.health_snapshot_delta import capture_health_baseline, health_delta
from atlas.health_snapshot_overlay import apply_overlay, file_stamp, snapshot_hash
from atlas.code_health import ANALYZER_VERSION
from atlas.code_health_policy import policy
from atlas import store
from test_grouping_cache import legacy_store


def sample():
    """Repeat equal values with independent dictionaries and explicit unknown components."""
    health = {'organization': {'score': None, 'coverage': {'local': {'status': 'complete'}}},
              'componentCoverage': {'organization': {'status': 'unknown'}}, 'measuredAt': 'before', 'score': None}
    return {'nodes': [{'id': 'a', 'health': deepcopy(health), 'documentationHealth': {'score': 20}},
                      {'id': 'b', 'health': deepcopy(health)}], 'edges': [], 'metadata': {}}


def persist_overlay(snapshot, graph, baseline, *, job='test'):
    """Write the exact public v2 envelope with a raw-snapshot hash fence."""
    changed, removed = health_delta(graph, baseline, 'after')
    overlay = {'formatVersion': 2, 'healthMeasuredAt': 'after', 'nodes': changed, 'removedFields': removed,
               'codeHealth': {'measuredAt': 'after'}, 'jobId': job, 'snapshotId': snapshot.parent.name,
               'sourceStamp': list(file_stamp(snapshot)), 'sourceHash': snapshot_hash(snapshot, file_stamp(snapshot)),
               'policyVersion': policy()['version'], 'analyzerVersion': ANALYZER_VERSION, 'measuredAt': 'job-time'}
    store.write_json(snapshot.with_name('code-health-overlay.json'), encode_health_payloads(overlay))
    return overlay


def test_codec_roundtrip_does_not_mutate_input_or_alias_decoded_profiles():
    graph = sample()
    before = deepcopy(graph)
    packed = encode_health_payloads(graph)
    assert graph == before
    assert len(packed[MARKER]['tables']['organization']) == 1
    decoded = decode_health_payloads(json.loads(json.dumps(packed)))
    assert decoded == graph
    assert MARKER not in decoded
    decoded['nodes'][0]['health']['organization']['score'] = 0
    assert decoded['nodes'][1]['health']['organization']['score'] is None
    assert graph == before
    overlay = {'nodes': {node['id']: node for node in graph['nodes']}}
    assert decode_health_payloads(json.loads(json.dumps(encode_health_payloads(overlay)))) == overlay


@pytest.mark.parametrize('bad', [True, -1, 999, '0', None])
def test_codec_rejects_invalid_reference_indexes(bad):
    packed = json.loads(json.dumps(encode_health_payloads(sample())))
    packed['nodes'][0]['health']['organization']['index'] = bad
    with pytest.raises(ValueError):
        decode_health_payloads(packed)


@pytest.mark.parametrize('change', ['version', 'field', 'shape', 'null-codec', 'envelope-extra', 'missing-table', 'table-ref'])
def test_codec_rejects_bad_versions_and_reference_shapes(change):
    packed = json.loads(json.dumps(encode_health_payloads(sample())))
    if change == 'version':
        packed[MARKER]['version'] = 'unknown'
    elif change == 'field':
        packed['nodes'][0]['health']['organization']['$healthPayload'] = 'componentCoverage'
    elif change == 'shape':
        packed['nodes'][0]['health']['organization']['extra'] = True
    elif change == 'envelope-extra':
        packed[MARKER]['future-extension'] = True
    elif change == 'missing-table':
        packed[MARKER]['tables'].pop('organization')
    elif change == 'table-ref':
        packed[MARKER]['tables']['organization'][0] = {'$healthPayload': 'organization', 'index': 0}
    else:
        packed[MARKER] = None
    with pytest.raises(ValueError):
        decode_health_payloads(packed)


def test_plain_legacy_graph_stays_readable_and_oversized_expansion_fails_closed(monkeypatch):
    import atlas.health_payload_codec as codec
    graph = sample()
    assert decode_health_payloads(deepcopy(graph)) == graph
    packed = encode_health_payloads(graph)
    monkeypatch.setattr(codec, 'MAX_EXPANDED_HEALTH_BYTES', 1)
    with pytest.raises(ValueError):
        decode_health_payloads(packed)
    assert encode_health_payloads(graph) == graph


def test_delta_preserves_null_removals_and_exact_explicit_timestamps(tmp_path):
    original = sample()
    snapshot = tmp_path / 'snapshot' / 'atlas-graph.json'
    store.write_json(snapshot, original)
    baseline = capture_health_baseline(original)
    expected = deepcopy(original)
    expected['nodes'][0]['health']['measuredAt'] = 'after'
    expected['nodes'][0]['documentationHealth'] = None
    expected['nodes'][1].pop('health')
    overlay = persist_overlay(snapshot, expected, baseline)
    assert 'health' not in overlay['nodes']['a']
    assert overlay['removedFields'] == {'b': ['health']}
    actual = apply_overlay(deepcopy(original), snapshot)
    assert actual['nodes'] == expected['nodes']
    assert actual['metadata']['codeHealth'] == overlay['codeHealth']
    # An explicit row's missing or custom timestamp is never replaced globally.
    for timestamp in (None, {'custom': True}, 'different', 'missing'):
        expected = deepcopy(original)
        if timestamp == 'missing':
            expected['nodes'][0]['health'].pop('measuredAt')
        else:
            expected['nodes'][0]['health']['measuredAt'] = timestamp
        persist_overlay(snapshot, expected, baseline, job=str(timestamp))
        assert apply_overlay(deepcopy(original), snapshot)['nodes'] == expected['nodes']


def test_delta_baseline_survives_inplace_evaluator_mutation_and_repeated_refresh(tmp_path):
    original = sample()
    snapshot = tmp_path / 'snapshot' / 'atlas-graph.json'
    store.write_json(snapshot, original)
    baseline = capture_health_baseline(original)
    original['nodes'][0]['health']['organization']['score'] = 7
    original['nodes'][0]['documentationHealth']['score'] = 7
    original['nodes'][0]['health']['measuredAt'] = 'after'
    first = persist_overlay(snapshot, original, baseline)
    assert first['nodes']['a']['health']['organization']['score'] == 7
    assert first['nodes']['a']['documentationHealth']['score'] == 7
    # A second refresh compares raw captured facts, never the preceding overlay.
    raw = json.loads(snapshot.read_text())
    raw['nodes'][0]['health']['measuredAt'] = 'after'
    second = persist_overlay(snapshot, raw, capture_health_baseline(json.loads(snapshot.read_text())), job='restored')
    assert 'a' not in second['nodes']
    assert apply_overlay(json.loads(snapshot.read_text()), snapshot)['nodes'] == raw['nodes']


def test_store_reads_encoded_canonical_and_apis_receive_expanded_health(tmp_path):
    state, project, snapshot, raw = legacy_store(tmp_path)
    try:
        raw['nodes'][0]['health'] = sample()['nodes'][0]['health']
        store.write_json(snapshot, encode_health_payloads(raw))
        assert MARKER in json.loads(snapshot.read_text())
        decoded = state._read_graph(snapshot)
        assert decoded == raw
        graph = state.get_graph(project)
        assert MARKER not in graph
        assert '$healthPayload' not in graph['nodes'][0]['health']['organization']
    finally:
        state.close()


def test_group_cache_refresh_restore_and_overlay_removal_rebase_health(tmp_path):
    state, project, snapshot, raw = legacy_store(tmp_path)
    raw['nodes'][0]['health'] = sample()['nodes'][0]['health']
    raw['nodes'][0]['documentationHealth'] = {'score': 20}
    store.write_json(snapshot, encode_health_payloads(raw))
    try:
        changed = deepcopy(raw)
        changed['nodes'][0]['health']['score'] = 1
        changed['nodes'][0]['health']['measuredAt'] = 'after'
        changed['nodes'][0]['documentationHealth'] = {'score': 1}
        persist_overlay(snapshot, changed, capture_health_baseline(raw), job='changed')
        assert state.get_graph(project)['nodes'][0]['health']['score'] == 1
        restored = deepcopy(raw)
        restored['nodes'][0]['health']['measuredAt'] = 'after'
        persist_overlay(snapshot, restored, capture_health_baseline(raw), job='restored')
        current = state.get_graph(project)
        assert current['nodes'][0]['health']['score'] is None
        assert current['nodes'][0]['documentationHealth'] == {'score': 20}
        snapshot.with_name('code-health-overlay.json').unlink()
        current = state.get_graph(project)
        assert 'healthRefresh' not in current['metadata']
        assert current['nodes'][0]['health'] == raw['nodes'][0]['health']
    finally:
        state.close()


def test_overlay_publishing_during_grouping_never_applies_delta_to_stale_health(tmp_path, monkeypatch):
    state, project, snapshot, raw = legacy_store(tmp_path)
    raw['nodes'][0]['health'] = sample()['nodes'][0]['health']
    store.write_json(snapshot, raw)
    changed = deepcopy(raw)
    changed['nodes'][0]['health']['score'] = 1
    changed['nodes'][0]['health']['measuredAt'] = 'after'
    persist_overlay(snapshot, changed, capture_health_baseline(raw), job='first')
    actual_group = store.enrich_graph

    def publish_during_group(graph, algorithm='louvain'):
        """Publish the restoring empty delta after the grouped graph acquired old health."""
        result = actual_group(graph, algorithm)
        restored = deepcopy(raw)
        restored['nodes'][0]['health']['measuredAt'] = 'after'
        persist_overlay(snapshot, restored, capture_health_baseline(raw), job='second')
        state._published_graph.clear()
        return result

    monkeypatch.setattr(store, 'enrich_graph', publish_during_group)
    try:
        result = state.get_graph(project)
        assert result['nodes'][0]['health']['score'] is None
        assert result['metadata']['healthRefresh']['jobId'] == 'second'
        from atlas.grouping_cache import cache_path
        assert not cache_path(snapshot, 'louvain').exists()
    finally:
        state.close()


def test_overlay_removal_during_grouping_clears_fields_absent_in_captured_base(tmp_path, monkeypatch):
    state, project, snapshot, raw = legacy_store(tmp_path)
    added = deepcopy(raw)
    added['nodes'][0]['health'] = sample()['nodes'][0]['health']
    added['nodes'][0]['health']['measuredAt'] = 'after'
    added['nodes'][0]['documentationHealth'] = {'score': 1}
    persist_overlay(snapshot, added, capture_health_baseline(raw), job='added')
    actual_group = store.enrich_graph

    def remove_during_group(graph, algorithm='louvain'):
        """Remove the derivative after grouping acquired fields absent from captured facts."""
        result = actual_group(graph, algorithm)
        snapshot.with_name('code-health-overlay.json').unlink()
        state._published_graph.clear()
        return result

    monkeypatch.setattr(store, 'enrich_graph', remove_during_group)
    try:
        result = state.get_graph(project)
        assert 'health' not in result['nodes'][0]
        assert 'documentationHealth' not in result['nodes'][0]
        assert 'healthRefresh' not in result['metadata']
    finally:
        state.close()


def test_store_rejects_corrupt_codec_and_overlay_version_without_leaking_refs(tmp_path):
    from atlas.health_snapshot_overlay import read_overlay
    state, _, snapshot, raw = legacy_store(tmp_path)
    try:
        raw['nodes'][0]['health'] = sample()['nodes'][0]['health']
        packed = encode_health_payloads(raw)
        packed['nodes'][0]['health']['organization']['index'] = True
        store.write_json(snapshot, packed)
        with pytest.raises(store.StoreError) as error:
            state._read_graph(snapshot)
        assert error.value.status == 409
        store.write_json(snapshot, raw)
        updated = deepcopy(raw)
        updated['nodes'][0]['health']['measuredAt'] = 'after'
        overlay = persist_overlay(snapshot, updated, capture_health_baseline(raw))
        overlay['formatVersion'] = 'future-version'
        store.write_json(snapshot.with_name('code-health-overlay.json'), overlay)
        assert read_overlay(snapshot) is None
    finally:
        state.close()


def test_group_cache_serialization_does_not_hold_store_wide_lock(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    state, project, _, _ = legacy_store(tmp_path)
    original = store.write_json

    def acquire_store_lock():
        """A different request must acquire the global lock during derived JSON writing."""
        with state.lock:
            return True

    def checked_write(path, value):
        """Observe only grouped cache writes, preserving the real atomic serialization."""
        if path.name.startswith('atlas-grouped-'):
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(acquire_store_lock).result(timeout=2)
        return original(path, value)

    monkeypatch.setattr(store, 'write_json', checked_write)
    try:
        assert state.get_graph(project)['nodes']
    finally:
        state.close()
