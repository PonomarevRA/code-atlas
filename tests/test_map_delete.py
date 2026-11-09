"""
Combined map deletion is a distinct confirmed authority; all destructive fixtures use private temporary state.
"""
import json
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.map_cleanup import MapCleanup
from atlas.map_cleanup_api import register_map_cleanup_routes
from atlas.map_cleanup_inventory import CleanupError
from atlas.map_cleanup_journal import recover, AREA
from atlas.data_store import DataMapStore
from atlas.store import ProjectStore, write_json
from test_map_cleanup import fixture, PROJECT, OTHER, publish_code, wait_done
from test_map_cleanup_data_recovery import import_data, Crash


@pytest.mark.parametrize('legacy', [False, True])
def test_combined_delete_removes_snapshots_and_registrations_preserves_sources_and_views(tmp_path, legacy):
    """
    Delete both map kinds across canonical/legacy data layouts without hiding historical authored views.

    Args:
        tmp_path: Private state/source fixture root.
        legacy: Exercise legacy data-map catalog and snapshot migration before deletion.
    """
    source = tmp_path / 'source'; source.mkdir()
    (source / 'a.py').write_text('def f(): return 1')
    state = tmp_path / 'state'
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    directory = publish_code(code, source)
    other = publish_code(code, source, OTHER, 'c' * 32)
    authored = directory.parent / 'views'; authored.mkdir()
    (authored / 'note.json').write_text('authored code view')
    item = import_data(data, source); map_id = item['id']; view_id = 'f' * 32
    views = data.root / map_id / 'views'; views.mkdir()
    (views / (view_id + '.json')).write_text(json.dumps({'title': 'Authored', 'snapshotId': item['snapshotId']}))
    data.close(); code.close()
    if legacy:
        (state / 'maps/data-maps').rename(state / 'data-maps')
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    cleanup = MapCleanup(code, data, code.maintenance)
    selections = [{'kind': 'code', 'id': PROJECT}, {'kind': 'data', 'id': map_id}]
    try:
        source_before = {p: p.read_bytes() for p in source.iterdir()}
        assert cleanup.availability('code', PROJECT)['deleteAvailable']
        plan = cleanup.preview(selections, action='delete-map')
        assert plan['action'] == 'delete-map' and plan['totals']['sizeBytes'] > 0
        assert 'project-and-data-map-registration' not in plan['preserved']
        cleanup.execute(plan['planId']); result = wait_done(cleanup, plan['planId'])
        assert result['status'] == 'completed' and result['mapsCleared'] and result['registrationRemoved']
        assert result['removedBytes'] == plan['totals']['sizeBytes'] and result['pendingPurgeBytes'] == 0
        assert PROJECT not in code.projects and OTHER in code.projects and map_id not in data.maps
        assert not directory.exists() and not (directory.parent / '_objects').exists()
        assert other.exists() and (authored / 'note.json').read_text() == 'authored code view'
        assert data.saved_view(map_id, view_id)['title'] == 'Authored'
        assert all(path.read_bytes() == value for path, value in source_before.items())
        assert cleanup.execute(plan['planId']) == result
    finally:
        cleanup.close(); data.close(); code.close()
    records = recover(state)
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    cleanup = MapCleanup(code, data, code.maintenance, recovered=records)
    try:
        assert PROJECT not in code.projects and map_id not in data.maps
        assert data.saved_view(map_id, view_id)['title'] == 'Authored'
        assert cleanup.status(plan['planId']) == result
    finally:
        cleanup.close(); data.close(); code.close()


@pytest.mark.parametrize('has_pointer', [False, True])
def test_combined_zero_target_registration_delete_is_terminal_without_worker(fixture, has_pointer):
    """
    Already-cleared and known-missing snapshots can remove metadata without a nonexistent purge worker.

    Args:
        fixture: Private real code map/service.
        has_pointer: Preserve a stale known pointer or model a previously cleared not-built registration.
    """
    code, data, cleanup, source, directory = fixture
    shutil.rmtree(directory.parent)
    if not has_pointer:
        for field in ('_graph', '_rawGraph', '_mcpGraph'):
            code.projects[PROJECT].pop(field, None)
        code.projects[PROJECT]['status'] = 'not-built'
        write_json(code.registry, code.projects)
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}], action='delete-map')
    result = cleanup.execute(plan['planId'])
    assert result['status'] == 'completed' and result['registrationRemoved'] and result['mapsCleared']
    assert result['removedBytes'] == 0 and cleanup.futures == {} and PROJECT not in code.projects
    assert recover(code.data_dir)[0]['action'] == 'delete-map'


@pytest.mark.parametrize('mutation', ['unknown', 'snapshot', 'link'])
def test_combined_preview_never_falls_back_or_mutates_newer_storage(fixture, mutation):
    """
    The combined action keeps clear's complete ownership/staleness policy without a metadata-only fallback.

    Args:
        fixture: Private code map/service.
        mutation: Unknown content, new snapshot or unsafe link introduced after preview.
    """
    code, data, cleanup, source, directory = fixture
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}], action='delete-map')
    if mutation == 'unknown':
        (directory / 'personal.txt').write_text('preserve')
    elif mutation == 'snapshot':
        publish_code(code, source, snapshot='d' * 32)
    else:
        (directory / 'foreign').symlink_to(source)
    with pytest.raises(CleanupError):
        cleanup.execute(plan['planId'])
    assert PROJECT in code.projects and directory.exists() and not (code.data_dir / AREA).exists()


@pytest.mark.parametrize('phase', ['after_journal', 'after_rename:0', 'after_catalog:projects.json', 'after_catalog:maps/data-maps/catalog.json'])
def test_combined_crash_recovers_registration_and_map_before_store_reload(fixture, phase):
    """
    An interrupted combined transaction restores both registration and physical snapshot ownership.

    Args:
        fixture: Private real snapshot/service.
        phase: Exact synthetic process-loss boundary before durable committed state.
    """
    code, data, cleanup, source, directory = fixture
    before = code.registry.read_bytes(); graph = (directory / 'atlas-graph.json').read_bytes()
    def crash(current):
        """
        Simulate process loss at the selected durable transaction phase.

        Args:
            current: Server-generated phase name.
        """
        if current == phase:
            raise Crash()
    cleanup.fault = crash
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}], action='delete-map')
    with pytest.raises(Crash):
        cleanup.execute(plan['planId'])
    operation = recover(code.data_dir)[0]
    assert code.registry.read_bytes() == before and (directory / 'atlas-graph.json').read_bytes() == graph
    assert operation['public']['status'] == 'failed'
    assert not operation['public']['mapsCleared'] and not operation['public']['registrationRemoved']


def test_combined_partial_restart_retry_preserves_new_same_id_registration(fixture):
    """
    Partial physical purge reports committed removal honestly and retries only original quarantine after restart.

    Args:
        fixture: Private snapshot/service with controllable synthetic purge fault.
    """
    code, data, cleanup, source, directory = fixture
    def fail(phase):
        """
        Fail only this operation's physical purge after its catalog commit.

        Args:
            phase: Trusted cleanup phase name.
        """
        if phase.startswith('before_purge:'):
            raise OSError('synthetic purge failure')
    cleanup.fault = fail
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}], action='delete-map')
    cleanup.execute(plan['planId']); result = wait_done(cleanup, plan['planId'])
    assert result['status'] == 'partial' and result['mapsCleared'] and result['registrationRemoved']
    assert result['removedBytes'] is None and result['pendingPurgeBytes'] is None and result['canRetry']
    assert PROJECT not in code.projects
    newer = publish_code(code, source, snapshot='e' * 32)
    records = recover(code.data_dir)
    resumed = MapCleanup(code, data, code.maintenance, recovered=records)
    try:
        final = wait_done(resumed, plan['planId'])
        assert final['status'] == 'completed' and final['registrationRemoved']
        assert newer.exists() and PROJECT in code.projects
        assert resumed.execute(plan['planId']) == final and newer.exists()
    finally:
        resumed.close()


def test_http_combined_action_is_bound_and_cannot_be_changed_at_confirmation(fixture):
    """
    Public DTO explicitly supports the combined operation but confirmation cannot change its action.

    Args:
        fixture: Private actual cleanup service exposed over its registered API.
    """
    code, data, cleanup, source, directory = fixture
    app = FastAPI(); register_map_cleanup_routes(app, cleanup)
    base = '/api/technical/map-cleanup'
    with TestClient(app) as client:
        plan = client.post(base + '/preview', json={'maps': [{'kind': 'code', 'id': PROJECT}], 'action': 'delete-map'}).json()
        path = base + '/' + plan['planId']
        assert client.post(path + '/execute', json={'confirm': True, 'action': 'forget-registration'}).status_code == 422
        response = client.post(path + '/execute', json={'confirm': True})
        assert response.status_code == 200
        result = wait_done(cleanup, plan['planId'])
        assert result['mapsCleared'] and result['registrationRemoved'] and result['action'] == 'delete-map'
        assert client.get(path).json() == result and PROJECT not in code.projects
