"""Storage cleanup preserves primary artifacts and every legacy exported snapshot path."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

import pytest

from atlas import store
from atlas.process_lock import ProcessLock
from atlas.project_storage import optimize_storage, snapshot_directory, storage_report
from test_jobs import fake_build

PROJECT = 'a' * 16
OLD, CURRENT = 'b' * 32, 'c' * 32


def storage_fixture(tmp_path):
    """Mix legacy/new layouts, immutable artifacts, derivable caches and unrecognized files."""
    root = tmp_path / 'data'
    old = root / PROJECT / OLD
    current = root / 'projects' / PROJECT / CURRENT
    for snapshot in (old, current):
        cache = snapshot / 'graphify-out/cache/ast/version'
        cache.mkdir(parents=True)
        (cache / 'entry.json').write_text('cache-only')
        for filename in ('atlas-graph.json', 'mcp-graph.json', 'graphify-out/graph.json'):
            (snapshot / filename).write_text('{"primary":"Пример"}')
        (snapshot / 'atlas-grouped-providers-v3-natural.json').write_text('grouping-only')
        (snapshot / 'atlas-grouped-user-notes.json').write_text('keep-note')
    (root / 'views').mkdir()
    (root / 'views/view.json').write_text('saved-view')
    (root / 'projects.json').write_text(json.dumps({PROJECT: {'id': PROJECT, '_graph': str(current / 'atlas-graph.json')}}))
    return root, old, current


def test_storage_report_is_read_only_and_cleanup_preserves_snapshots(tmp_path):
    """Only AST caches and historical known grouping names disappear; current rendering stays warm."""
    root, old, current = storage_fixture(tmp_path)
    protected = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    report = storage_report(root)
    assert report['snapshotCount'] == 2 and report['projectCount'] == 1
    assert len(report['candidates']) == 3
    assert all(path.read_bytes() == value for path, value in protected.items())
    result = optimize_storage(root)
    assert result['status'] == 'completed' and result['removedTargets'] == 3
    assert result['removedBytes'] == report['reclaimableBytes']
    assert not (old / 'graphify-out/cache').exists()
    assert not (current / 'graphify-out/cache').exists()
    assert not (old / 'atlas-grouped-providers-v3-natural.json').exists()
    for path, value in protected.items():
        if '/cache/' not in str(path) and path != old / 'atlas-grouped-providers-v3-natural.json':
            assert path.read_bytes() == value
    assert optimize_storage(root)['removedTargets'] == 0


def test_cleanup_rejects_open_atlas_before_removing_any_cache(tmp_path):
    """The same cross-platform lock used by the server protects a live extractor."""
    root, old, _ = storage_fixture(tmp_path)
    with ProcessLock(root / 'server.lock'):
        with pytest.raises(RuntimeError, match='другим процессом'):
            optimize_storage(root)
    assert (old / 'graphify-out/cache').exists()


def test_symlink_candidates_and_unknown_projects_are_never_followed(tmp_path):
    """A snapshot/cache alias cannot turn cache cleanup into removal of another repository."""
    root, old, current = storage_fixture(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    secret = outside / 'primary.json'
    secret.write_text('do-not-touch')
    import shutil
    shutil.rmtree(old / 'graphify-out/cache')
    (old / 'graphify-out/cache').symlink_to(outside, target_is_directory=True)
    (current / 'graphify-out/cache/ast/version/external').symlink_to(secret)
    (root / PROJECT / ('d' * 32)).symlink_to(outside, target_is_directory=True)
    unknown = root / ('e' * 16) / ('f' * 32) / 'graphify-out/cache'
    unknown.mkdir(parents=True)
    (unknown / 'entry').write_text('unregistered')
    assert all(row['path'] != f'{PROJECT}/{OLD}/graphify-out/cache' for row in storage_report(root)['candidates'])
    optimize_storage(root)
    assert secret.read_text() == 'do-not-touch'
    assert (unknown / 'entry').read_text() == 'unregistered'
    assert (old / 'graphify-out/cache').is_symlink()


def test_malformed_registry_and_cleanup_failures_are_explicit(tmp_path, monkeypatch):
    """Bad metadata aborts before deleting; a filesystem failure reports partial instead of invented savings."""
    root, old, _ = storage_fixture(tmp_path)
    registry = root / 'projects.json'
    valid = registry.read_bytes()
    registry.write_text(json.dumps({PROJECT: 'invalid'}))
    with pytest.raises(RuntimeError, match='registry'):
        optimize_storage(root)
    assert (old / 'graphify-out/cache').exists()
    registry.write_bytes(valid)
    import atlas.project_storage as storage

    def unavailable(path):
        raise OSError('fixture unavailable')

    monkeypatch.setattr(storage.shutil, 'rmtree', unavailable)
    result = optimize_storage(root)
    assert result['status'] == 'partial' and len(result['errors']) == 2
    assert result['removedTargets'] == 1
    assert result['removedBytes'] == len('grouping-only')


def test_new_storage_path_rejects_escapes_and_supports_legacy_registered_ids(tmp_path):
    """Directory organization does not break valid old registry IDs or follow a replacement projects link."""
    assert snapshot_directory(tmp_path, 'legacy-fixture', OLD) == tmp_path / 'maps/projects/legacy-fixture' / OLD
    with pytest.raises(ValueError):
        snapshot_directory(tmp_path, '../outside', OLD)
    (tmp_path / 'maps').symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        snapshot_directory(tmp_path, PROJECT, OLD)


def build_fixture(tmp_path, monkeypatch):
    """Use a deterministic publication without grouping processes or source analysis side effects."""
    state = store.ProjectStore(tmp_path / 'state')
    source = tmp_path / 'repo'
    source.mkdir()

    def extracted(project, output):
        graph = fake_build(project, output)
        cache = output / 'graphify-out/cache/ast'
        cache.mkdir(parents=True)
        (cache / 'entry.json').write_text('temporary AST')
        graph['nodes'][0]['attributeFacts'] = {'status': 'parsed', 'groups': []}
        return graph

    monkeypatch.setattr(store, 'build_graph', extracted)
    monkeypatch.setattr(store, 'enrich_snapshot', lambda graph, root, **kwargs: graph)
    monkeypatch.setattr(store, 'enrich_graph', lambda graph: graph)
    result = state.add(str(source))
    state.executor.shutdown(wait=True)
    return state, result['project']['id']


def test_new_publication_is_canonical_and_mcp_projection_is_lazy_and_single(tmp_path, monkeypatch):
    """Agent reads need no second full graph; Graphify exports retain complete data and concurrency isolation."""
    calls = []
    original = store.build_mcp_graph

    def project(graph):
        calls.append(True)
        return original(graph)

    monkeypatch.setattr(store, 'build_mcp_graph', project)
    state, project_id = build_fixture(tmp_path, monkeypatch)
    metadata = state.projects[project_id]
    snapshot = Path(metadata['_graph'])
    assert snapshot.is_relative_to(state.data_dir / 'maps' / 'projects' / project_id)
    assert not Path(metadata['_mcpGraph']).exists() and not calls
    assert not (snapshot.parent / 'graphify-out/cache').exists()
    raw = Path(metadata['_rawGraph']).read_bytes()
    canonical = snapshot.read_bytes()
    assert state.search_project_nodes(project_id, None, 'First')['total'] == 1
    assert not calls
    with ThreadPoolExecutor(max_workers=4) as workers:
        paths = list(workers.map(lambda _: state.published_mcp_graph(project_id), range(4)))
    assert len(calls) == 1 and len(set(paths)) == 1
    graph = json.loads(paths[0].read_text())
    assert graph['nodes'][0]['attributeFacts']['status'] == 'parsed'
    assert graph['nodes'][0]['attributes']['atlas.attributesStatus'] == 'parsed'
    assert snapshot.read_bytes() == canonical and Path(metadata['_rawGraph']).read_bytes() == raw
    paths[0].unlink()
    assert state.published_mcp_graph(project_id).exists() and len(calls) == 2
    state.close()


def test_legacy_missing_projection_stays_an_error_and_unsafe_output_fails_job(tmp_path, monkeypatch):
    """Only explicitly lazy files regenerate; setup failures still release the job queue."""
    state, project_id = build_fixture(tmp_path, monkeypatch)
    state.projects[project_id].pop('_mcpGraphLazy')
    with pytest.raises(store.StoreError, match='отсутствует'):
        state.published_mcp_graph(project_id)
    state.close()
    other = tmp_path / 'bad-state'
    other.mkdir()
    (other / 'maps').symlink_to(tmp_path / 'state', target_is_directory=True)
    state = store.ProjectStore(other)
    result = state.add(str(tmp_path / 'repo'))
    state.close()
    assert state.jobs[result['job']['id']]['status'] == 'failed'
    assert state.active_job is None


def test_cleanup_failure_does_not_fail_a_valid_publication(tmp_path, monkeypatch, caplog):
    """A cache permission error is recoverable housekeeping, not loss of canonical graph facts."""
    import atlas.project_storage as storage

    def unavailable(snapshot):
        raise OSError('fixture cleanup error')

    monkeypatch.setattr(storage, 'discard_extraction_cache', unavailable)
    state, project_id = build_fixture(tmp_path, monkeypatch)
    assert state.projects[project_id]['status'] == 'completed'
    assert Path(state.projects[project_id]['_graph']).is_file()
    assert 'Extraction cache cleanup failed' in caplog.text
    state.close()


def test_compact_json_and_cli_round_trip(tmp_path):
    """Unicode values remain exact; offline CLI reporting does not start the HTTP server."""
    root, _, _ = storage_fixture(tmp_path)
    value = {'description': 'Исходник', 'items': [1, None, 'a b']}
    path = tmp_path / 'compact.json'
    store.write_json(path, value)
    assert json.loads(path.read_text()) == value
    assert path.stat().st_size < len(json.dumps(value, ensure_ascii=False).encode())
    for option in ('--storage-report', '--optimize-storage'):
        result = subprocess.run([sys.executable, '-m', 'atlas.cli', '--data-dir', str(root), option],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['projectCount'] == 1


def test_concurrent_json_writers_use_independent_atomic_staging(tmp_path, monkeypatch):
    """Server and CLI can materialize the same projection without truncating or unlinking each other's staging."""
    from threading import Barrier
    ready = Barrier(2)
    original = Path.write_text
    temporary_names = []

    def interleaved(path, text, *args, **kwargs):
        temporary_names.append(path.name)
        ready.wait(timeout=5)
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', interleaved)
    target = tmp_path / 'projection.json'
    values = [{'value': number, 'payload': str(number) * 100_000} for number in (1, 2)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda value: store.write_json(target, value), values))
    assert len(set(temporary_names)) == 2
    assert json.loads(target.read_text()) in values
    assert not list(tmp_path.glob('*.tmp'))


def test_storage_inventory_counts_maps_and_both_legacy_roots(tmp_path):
    """Mixed layouts are counted once each and cleanup preserves all published evidence."""
    root, old, previous = storage_fixture(tmp_path)
    canonical = root / 'maps/projects' / PROJECT / ('d' * 32)
    (canonical / 'graphify-out/cache').mkdir(parents=True)
    (canonical / 'graphify-out/cache/ast').write_text('derived')
    (canonical / 'atlas-graph.json').write_text('canonical primary')
    report = storage_report(root)
    assert report['snapshotCount'] == 3
    assert report['newProjectsDirectory'] == str(root / 'maps/projects')
    assert any(row['path'].startswith('maps/projects/') for row in report['candidates'])
    optimize_storage(root)
    assert (canonical / 'atlas-graph.json').read_text() == 'canonical primary'
    assert (old / 'atlas-graph.json').exists() and (previous / 'atlas-graph.json').exists()


def test_new_snapshot_directory_preserves_file_conflicts(tmp_path):
    """A user file named maps is an explicit failure, never replaced by a directory."""
    (tmp_path / 'maps').write_text('unknown object')
    with pytest.raises(ValueError, match='conflict'):
        snapshot_directory(tmp_path, PROJECT, OLD)
    assert (tmp_path / 'maps').read_text() == 'unknown object'
