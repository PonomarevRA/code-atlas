"""
Legacy/missing-snapshot repair and metadata-only forgetting use isolated fixtures exclusively.
"""
from copy import deepcopy
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
from test_map_cleanup import fixture, PROJECT, publish_code, wait_done
from test_map_cleanup_data_recovery import import_data, Crash


@pytest.mark.parametrize('layout', ['canonical', 'projects', 'legacy'])
def test_missing_owned_snapshot_resets_pointers_without_deletion(tmp_path, layout):
    """
    Missing legitimate layouts permit a zero-target reset without queuing a physical worker.

    Args:
        tmp_path: Private state/source root.
        layout: Each previously supported code snapshot layout.
    """
    source = tmp_path / 'source'; source.mkdir()
    code = ProjectStore(tmp_path / 'state')
    data = DataMapStore(code.data_dir, maintenance=code.maintenance)
    directory = publish_code(code, source, layout=layout)
    shutil.rmtree(directory.parent)
    cleanup = MapCleanup(code, data, code.maintenance)
    try:
        row = cleanup.availability('code', PROJECT)
        assert row['available'] and row['reason'] == 'missing-snapshot' and row['forgetAvailable']
        plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
        assert plan['totals']['sizeBytes'] == 0
        result = cleanup.execute(plan['planId'])
        assert result['status'] == 'completed' and result['mapsCleared']
        assert result['registrationRemoved'] is False and result['removedBytes'] == 0
        assert cleanup.futures == {} and not directory.exists()
        assert code.projects[PROJECT]['status'] == 'not-built'
        assert '_graph' not in code.projects[PROJECT]
        assert cleanup.execute(plan['planId']) == result
    finally:
        cleanup.close(); data.close(); code.close()


@pytest.mark.parametrize('mutation', ['pointer-file', 'snapshot', 'symlink', 'catalog'])
def test_missing_snapshot_preview_fences_reappearance(fixture, mutation):
    """
    Missing-path authorization does not permit a later file, snapshot, link or changed registration.

    Args:
        fixture: Isolated real code map service.
        mutation: A path/catalog transition after preview and before confirmation.
    """
    code, data, cleanup, source, directory = fixture
    shutil.rmtree(directory)
    shutil.rmtree(directory.parent / '_objects')
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    if mutation == 'pointer-file':
        directory.mkdir(); (directory / 'atlas-graph.json').write_text('{}')
    elif mutation == 'snapshot':
        publish_code(code, source, snapshot='b' * 32)
    elif mutation == 'symlink':
        directory.symlink_to(source, target_is_directory=True)
    else:
        code.projects[PROJECT]['name'] = 'Changed'
        write_json(code.registry, code.projects)
    with pytest.raises(CleanupError) as error:
        cleanup.execute(plan['planId'])
    assert error.value.code in {'CLEANUP_STALE', 'CLEANUP_UNSAFE'}
    assert not (code.data_dir / AREA).exists()


@pytest.mark.parametrize('pointer', ['foreign', 'wrong-leaf', 'wrong-snapshot', 'wrong-role', 'link'])
def test_absent_pointer_exceptions_stay_within_exact_owner_and_role(fixture, pointer):
    """
    Absence never legalizes arbitrary paths, an unexpected leaf, role confusion or a link.

    Args:
        fixture: Private code map/service.
        pointer: Adversarial catalog pointer variant.
    """
    code, data, cleanup, source, directory = fixture
    candidates = {'foreign': source / 'missing.json', 'wrong-leaf': directory / 'authored.json',
                  'wrong-snapshot': directory.parent / 'not-a-snapshot' / 'atlas-graph.json',
                  'wrong-role': directory / 'mcp-graph.json', 'link': directory / 'atlas-graph.json'}
    if pointer == 'link':
        (directory / 'atlas-graph.json').unlink()
        (directory / 'atlas-graph.json').symlink_to(source / 'missing')
    code.projects[PROJECT]['_graph'] = str(candidates[pointer])
    write_json(code.registry, code.projects)
    with pytest.raises(CleanupError) as error:
        cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    assert error.value.code == 'CLEANUP_UNSAFE'
    assert cleanup.availability('code', PROJECT)['forgetAvailable']
    assert not (code.data_dir / AREA).exists()


def test_semantic_status_is_generated_but_unknown_files_remain_blocked(fixture):
    """
    Admit the exact coordinator output leaf without extending permission to similar authored files.

    Args:
        fixture: Private real snapshot and cleanup service.
    """
    code, data, cleanup, source, directory = fixture
    (directory / 'semantic-status.json').write_text('{"version":"atlas-semantic-status-v1"}')
    (directory / 'semantic-status-notes.json').write_text('authored')
    with pytest.raises(CleanupError):
        cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    (directory / 'semantic-status-notes.json').unlink()
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    cleanup.execute(plan['planId'])
    assert wait_done(cleanup, plan['planId'])['status'] == 'completed'


@pytest.mark.parametrize('legacy', [False, True])
def test_forget_preserves_all_files_and_saved_views_after_restart(tmp_path, legacy):
    """
    Remove only code/data registration metadata and keep historical views without legacy resurrection.

    Args:
        tmp_path: Private code/data/source fixture root.
        legacy: Exercise authoritative legacy data catalog fallback before canonical removal.
    """
    source = tmp_path / 'source'; source.mkdir()
    state = tmp_path / 'state'
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    directory = publish_code(code, source)
    item = import_data(data, source); map_id = item['id']
    view_id = 'f' * 32
    view_dir = data.root / map_id / 'views'; view_dir.mkdir()
    (view_dir / (view_id + '.json')).write_text(json.dumps({'title': 'Historical', 'snapshotId': item['snapshotId']}))
    (directory / 'personal.txt').write_text('unknown authored content')
    data.close(); code.close()
    if legacy:
        (state / 'maps/data-maps').rename(state / 'data-maps')
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    cleanup = MapCleanup(code, data, code.maintenance)
    before = {p.relative_to(state): p.read_bytes() for p in state.rglob('*') if p.is_file()
              and p.name not in {'projects.json', 'catalog.json'}}
    try:
        # No map tree walk is permitted in metadata-only mode, even on an unsafe clear target.
        cleanup.protected_roots = lambda: pytest.fail('Forget must not inspect protected source roots')
        plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}, {'kind': 'data', 'id': map_id}], action='forget-registration')
        assert plan['action'] == 'forget-registration' and plan['totals']['sizeBytes'] == 0
        result = cleanup.execute(plan['planId'])
        assert result['registrationRemoved'] and not result['mapsCleared']
        assert result['status'] == 'completed' and result['removedBytes'] == 0
        assert cleanup.futures == {} and not code.projects and not data.maps
        assert data.saved_view(map_id, view_id)['title'] == 'Historical'
        assert all((state / path).read_bytes() == content for path, content in before.items())
        assert not list((state / AREA / plan['planId']).glob('target-*'))
        # Same-ID re-registration is newer data and survives replay of the old forget authorization.
        publish_code(code, source, snapshot='d' * 32)
        assert cleanup.execute(plan['planId']) == result and PROJECT in code.projects
    finally:
        cleanup.close(); data.close(); code.close()
    records = recover(state)
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    cleanup = MapCleanup(code, data, code.maintenance, recovered=records)
    try:
        assert map_id not in data.maps and PROJECT in code.projects
        assert data.saved_view(map_id, view_id)['title'] == 'Historical'
        assert cleanup.execute(plan['planId'])['registrationRemoved']
        assert PROJECT in code.projects and cleanup.futures == {}
    finally:
        cleanup.close(); data.close(); code.close()


@pytest.mark.parametrize('phase', ['after_journal', 'after_catalog:projects.json', 'after_catalog:maps/data-maps/catalog.json'])
def test_forget_crash_restores_catalogs_without_touching_files(fixture, phase):
    """
    Recover metadata-only precommit interruption after every catalog boundary.

    Args:
        fixture: Private registered code map and service.
        phase: Synthetic process-loss boundary without ordinary rollback handling.
    """
    code, data, cleanup, source, directory = fixture
    before = code.registry.read_bytes()
    graph = (directory / 'atlas-graph.json').read_bytes()
    def crash(current):
        """
        Simulate process loss only at the selected durable transaction boundary.

        Args:
            current: Server-generated phase name.
        """
        if current == phase:
            raise Crash()
    cleanup.fault = crash
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}], action='forget-registration')
    with pytest.raises(Crash):
        cleanup.execute(plan['planId'])
    recovered = recover(code.data_dir)
    assert code.registry.read_bytes() == before
    assert (directory / 'atlas-graph.json').read_bytes() == graph
    assert recovered[0]['public']['registrationRemoved'] is False
    assert recovered[0]['public']['status'] == 'failed'


def test_forget_http_mode_validation_stale_and_exact_replay(fixture):
    """
    Explicit action is bound to the preview and cannot be replaced at confirmation.

    Args:
        fixture: Isolated actual service exposed through its public HTTP routes.
    """
    code, data, cleanup, source, directory = fixture
    app = FastAPI(); register_map_cleanup_routes(app, cleanup)
    base = '/api/technical/map-cleanup'
    maps = [{'kind': 'code', 'id': PROJECT}]
    with TestClient(app) as client:
        assert client.post(base + '/preview', json={'maps': maps, 'action': 'delete-source'}).status_code == 422
        plan = client.post(base + '/preview', json={'maps': maps, 'action': 'forget-registration'}).json()
        execute = base + '/' + plan['planId'] + '/execute'
        assert client.post(execute, json={'confirm': True, 'action': 'clear-snapshots'}).status_code == 422
        code.projects[PROJECT]['name'] = 'New registration revision'; write_json(code.registry, code.projects)
        assert client.post(execute, json={'confirm': True}).json()['code'] == 'CLEANUP_STALE'
        assert PROJECT in code.projects and directory.exists()
        plan = client.post(base + '/preview', json={'maps': maps, 'action': 'forget-registration'}).json()
        execute = base + '/' + plan['planId'] + '/execute'
        result = client.post(execute, json={'confirm': True}).json()
        assert result['action'] == 'forget-registration' and result['registrationRemoved']
        assert client.get(base + '/' + plan['planId']).json() == result
        assert client.post(execute, json={'confirm': True}).json() == result
        assert directory.exists() and PROJECT not in code.projects


def test_forget_unsafe_storage_and_busy_boundary_do_not_follow_paths(fixture):
    """
    Metadata removal never follows an invalid private pointer and still excludes active workers.

    Args:
        fixture: Private source/map service with no production storage access.
    """
    code, data, cleanup, source, directory = fixture
    outside = source / 'authored.json'; outside.write_text('preserve')
    code.projects[PROJECT]['_graph'] = str(outside)
    write_json(code.registry, code.projects)
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}], action='forget-registration')
    release = cleanup.maintenance.enter(worker=True)
    try:
        with pytest.raises(CleanupError) as error:
            cleanup.execute(plan['planId'])
        assert error.value.code == 'CLEANUP_BUSY' and error.value.retryable
        assert PROJECT in code.projects and not (code.data_dir / AREA).exists()
    finally:
        release()
    result = cleanup.execute(plan['planId'])
    assert result['registrationRemoved'] and result['removedBytes'] == 0
    assert outside.read_text() == 'preserve' and directory.exists()


def test_recovered_committed_forget_finishes_without_worker_or_new_catalog_mutation(fixture):
    """
    The committed-to-terminal crash window completes only its empty purge on restart.

    Args:
        fixture: Private code map and real service used to prepare a durable confirmed operation.
    """
    code, data, cleanup, source, directory = fixture
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}], action='forget-registration')
    result = cleanup.execute(plan['planId'])
    operation = deepcopy(cleanup.operations[plan['planId']])
    operation['phase'] = 'committed'; operation['public']['status'] = 'purging'
    from atlas import map_cleanup_journal as journal
    journal.save(code.data_dir, operation)
    publish_code(code, source, snapshot='e' * 32)
    recovered = recover(code.data_dir)
    resumed = MapCleanup(code, data, code.maintenance, recovered=recovered)
    try:
        assert resumed.status(plan['planId']) == result
        assert resumed.futures == {} and PROJECT in code.projects
        assert directory.exists()
    finally:
        resumed.close()


def test_recovery_rejects_forget_journal_with_physical_targets(fixture):
    """
    A changed journal mode cannot repurpose an old snapshot-deletion authorization as forgetting.

    Args:
        fixture: Private real snapshot/service.
    """
    code, data, cleanup, source, directory = fixture
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    cleanup.execute(plan['planId']); wait_done(cleanup, plan['planId'])
    from atlas import map_cleanup_journal as journal
    operation = deepcopy(cleanup.operations[plan['planId']])
    operation['action'] = operation['public']['action'] = 'forget-registration'
    journal.save(code.data_dir, operation)
    with pytest.raises(CleanupError) as error:
        recover(code.data_dir)
    assert error.value.code == 'CLEANUP_RECOVERY_REQUIRED'


@pytest.mark.parametrize('directory', [False, True])
def test_unknown_snapshot_content_returns_only_fixed_safe_http_reason(fixture, directory):
    """
    Explain unknown content without exposing its filename/path or weakening the refusal contract.

    Args:
        fixture: Isolated real code snapshot/service exposed through actual cleanup routes.
        directory: Distinguish an unrecognized file from an unrecognized empty directory.
    """
    code, data, cleanup, source, snapshot = fixture
    private_name = 'private-sensitive-content'
    target = snapshot / private_name
    if directory:
        target.mkdir()
    else:
        target.write_text('authored content')
    app = FastAPI(); register_map_cleanup_routes(app, cleanup)
    with TestClient(app) as client:
        response = client.post('/api/technical/map-cleanup/preview', json={'maps': [{'kind': 'code', 'id': PROJECT}]})
    assert response.status_code == 409
    value = response.json()
    assert value['code'] == 'CLEANUP_UNSAFE' and value['retryable'] is False
    assert value['reason'] == ('unrecognized-snapshot-directory' if directory else 'unrecognized-snapshot-file')
    assert private_name not in response.text and str(code.data_dir) not in response.text
    assert target.exists() and PROJECT in code.projects and not (code.data_dir / AREA).exists()


def test_other_unsafe_reason_stays_unspecified_and_unrecognized_reason_is_not_exposed(fixture):
    """
    Leave other unsafe causes generic and refuse arbitrary diagnostic text at the public boundary.

    Args:
        fixture: Private code map/service, never production user data.
    """
    code, data, cleanup, source, directory = fixture
    code.projects[PROJECT]['_graph'] = str(source / 'foreign.json'); write_json(code.registry, code.projects)
    app = FastAPI(); register_map_cleanup_routes(app, cleanup)
    with TestClient(app) as client:
        response = client.post('/api/technical/map-cleanup/preview', json={'maps': [{'kind': 'code', 'id': PROJECT}]})
    assert response.status_code == 409 and response.json()['code'] == 'CLEANUP_UNSAFE'
    assert 'reason' not in response.json()
    assert CleanupError('CLEANUP_UNSAFE', 'Safe message', reason='/private/path').reason is None
