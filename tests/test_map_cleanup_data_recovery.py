"""
Actual data-map saved views and crash recovery remain compatible with explicit snapshot cleanup.
"""
import json
from pathlib import Path
import shutil
import time
import pytest

from atlas.store import ProjectStore
from atlas.data_store import DataMapStore
from atlas.data_models import DataInput, DataError
from atlas.map_cleanup import MapCleanup
from atlas.map_cleanup_inventory import CleanupError
from atlas.map_cleanup_journal import recover, AREA
from test_map_cleanup import PROJECT, SNAPSHOT, publish_code, wait_done


def import_data(data, source):
    """
    Import one real CSV under the existing explicit-create lifecycle.

    Args:
        data: Isolated DataMapStore sharing the code-store maintenance gate.
        source: Separate source input root.
    Returns:
        Ready public map metadata after its queued worker fully releases admission.
    """
    (source / 'rows.csv').write_text('id,name\n1,Alice\n2,Bob\n')
    result = data.create(DataInput(files=['rows.csv']), source)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = data.job(result['job']['id'])
        with data.maintenance.lock:
            active = data.maintenance.workers
        if job['status'] == 'completed' and not active:
            return data.overview(result['map']['id'])
        if job['status'] in {'failed', 'cancelled'}:
            raise AssertionError(job)
        time.sleep(.005)
    raise AssertionError('Data fixture import did not complete')


@pytest.mark.parametrize('legacy', [False, True])
def test_data_reset_preserves_authored_views_and_restart_does_not_resurrect_snapshot(tmp_path, legacy):
    """
    Retain the same data-map ID/options and authored views in both storage layouts after reset.

    Args:
        tmp_path: Private fixture root.
        legacy: Move the actual published catalog/snapshot into the supported legacy layout first.
    """
    source = tmp_path / 'source'; source.mkdir()
    state = tmp_path / 'state'
    code = ProjectStore(state)
    data = DataMapStore(state, maintenance=code.maintenance)
    item = import_data(data, source)
    map_id, snapshot = item['id'], item['snapshotId']
    view_id = 'f' * 32
    views = data.root / map_id / 'views'; views.mkdir()
    (views / (view_id + '.json')).write_text(json.dumps({'title':'authored view','snapshotId':snapshot}))
    data.close(); code.close()
    if legacy:
        (state / 'maps/data-maps').rename(state / 'data-maps')
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    cleanup = MapCleanup(code, data, code.maintenance)
    try:
        original = dict(data.maps[map_id])
        plan = cleanup.preview([{'kind':'data','id':map_id}])
        cleanup.execute(plan['planId']); final = wait_done(cleanup, plan['planId'])
        assert final['status'] == 'completed'
        assert data.maps[map_id]['status'] == 'not-built' and data.maps[map_id]['snapshotId'] is None
        for key in ['root','files','options','title','projectId']:
            assert data.maps[map_id][key] == original.get(key)
        assert data.saved_view(map_id, view_id)['title'] == 'authored view'
        with pytest.raises(DataError):
            data.snapshot(map_id, snapshot)
        assert data.registry.is_file(), 'Reset catalog must become canonical before legacy fallback could resurrect it'
    finally:
        cleanup.close(); data.close(); code.close()
    recovered = recover(state)
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    cleanup = MapCleanup(code, data, code.maintenance, recovered=recovered)
    try:
        assert data.maps[map_id]['status'] == 'not-built' and data.maps[map_id]['snapshotId'] is None
        assert data.saved_view(map_id, view_id)['snapshotId'] == snapshot
        assert cleanup.status(plan['planId'])['mapsCleared'] is True
        # The retained registration can explicitly build the same ID again.
        refreshed = data.refresh(map_id, force=True)
        assert refreshed['map']['id'] == map_id
    finally:
        cleanup.close(); data.close(); code.close()


class Crash(BaseException):
    """
    Simulate abrupt process loss without executing the producer's ordinary exception rollback.
    """


@pytest.mark.parametrize('phase', ['after_journal','after_rename:0','after_rename:1','after_catalog:projects.json','after_catalog:maps/data-maps/catalog.json'])
def test_interrupted_precommit_recovers_both_catalogs_before_store_reload(tmp_path, phase):
    """
    A durable journal restores the exact precommit state after every rename/catalog boundary.

    Args:
        tmp_path: Isolated data and source scopes.
        phase: Trusted synthetic crash boundary in a confirmed transaction.
    """
    source = tmp_path / 'source'; source.mkdir()
    state = tmp_path / 'state'
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    directory = publish_code(code, source)
    item = import_data(data, source)
    before_code = code.registry.read_bytes(); before_data = data.registry.read_bytes()
    graph_before = (directory / 'atlas-graph.json').read_bytes()
    def crash(current):
        """
        Abort at the exact transaction phase without ordinary exception handling.

        Args:
            current: Server-authored fault boundary.
        """
        if current == phase:
            raise Crash()
    cleanup = MapCleanup(code, data, code.maintenance, fault=crash)
    plan = cleanup.preview([{'kind':'code','id':PROJECT},{'kind':'data','id':item['id']}])
    try:
        with pytest.raises(Crash):cleanup.execute(plan['planId'])
    finally:
        cleanup.close(); data.close(); code.close()
    records = recover(state)
    assert code.registry.read_bytes() == before_code and data.registry.read_bytes() == before_data
    assert (directory / 'atlas-graph.json').read_bytes() == graph_before
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    cleanup = MapCleanup(code, data, code.maintenance, recovered=records)
    try:
        assert cleanup.status(plan['planId'])['status'] == 'failed'
        assert data.overview(item['id'])['records'] == 2
        assert code.comparison_snapshot(PROJECT)[1]['metadata']['snapshotId'] == SNAPSHOT
    finally:
        cleanup.close(); data.close(); code.close()


def test_committed_partial_operation_resumes_only_quarantine_after_restart(tmp_path):
    """
    Restart status/recovery never reselects new data from a completed/reset catalog.

    Args:
        tmp_path: Private fixture state and source roots.
    """
    source = tmp_path / 'source'; source.mkdir()
    state = tmp_path / 'state'
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    directory = publish_code(code, source)
    def fail(phase):
        """
        Hold only physical quarantine cleanup in a retryable state.

        Args:
            phase: Trusted server fault boundary.
        """
        if phase.startswith('before_purge:'):raise OSError('synthetic locked file')
    cleanup = MapCleanup(code, data, code.maintenance, fault=fail)
    plan = cleanup.preview([{'kind':'code','id':PROJECT}]); cleanup.execute(plan['planId'])
    assert wait_done(cleanup, plan['planId'])['status'] == 'partial'
    cleanup.close(); data.close(); code.close()
    records = recover(state)
    code = ProjectStore(state); data = DataMapStore(state, maintenance=code.maintenance)
    newer = publish_code(code, source, snapshot='e'*32)
    cleanup = MapCleanup(code, data, code.maintenance, recovered=records)
    try:
        assert wait_done(cleanup, plan['planId'])['status'] == 'completed'
        assert newer.exists() and code.projects[PROJECT]['_graph'] == str(newer/'atlas-graph.json')
    finally:
        cleanup.close(); data.close(); code.close()


def test_unknown_material_in_owned_quarantine_is_never_purged(tmp_path):
    """
    Retry cannot broaden original file authorization even inside the private quarantine area.

    Args:
        tmp_path: Isolated fixture root with a deliberately failed first purge.
    """
    source = tmp_path/'source';source.mkdir();state=tmp_path/'state'
    code=ProjectStore(state);data=DataMapStore(state,maintenance=code.maintenance)
    publish_code(code,source)
    def fail(phase):
        """
        Fail the first purge so the test may add a clearly foreign file.

        Args:
            phase: Trusted server phase.
        """
        if phase.startswith('before_purge:'):raise OSError('synthetic fault')
    cleanup=MapCleanup(code,data,code.maintenance,fault=fail)
    try:
        plan=cleanup.preview([{'kind':'code','id':PROJECT}]);cleanup.execute(plan['planId'])
        assert wait_done(cleanup,plan['planId'])['status']=='partial'
        foreign=state/AREA/plan['planId']/'target-0'/'foreign.txt';foreign.write_text('never authorized')
        cleanup.fault=lambda _:None
        cleanup.execute(plan['planId'])
        assert wait_done(cleanup,plan['planId'])['status']=='partial' and foreign.read_text()=='never authorized'
    finally:
        cleanup.close();data.close();code.close()


def test_material_added_after_validation_survives_leaf_only_purge(tmp_path):
    """
    Reproduce the exact postvalidation race without recursive deletion of newly introduced content.

    Args:
        tmp_path: Isolated synthetic map/quarantine state.
    """
    source=tmp_path/'source';source.mkdir();state=tmp_path/'state'
    code=ProjectStore(state);data=DataMapStore(state,maintenance=code.maintenance)
    publish_code(code,source)
    foreign=[]
    def insert(phase):
        """
        Add a foreign authored file after whole-tree validation but before original-name purge.

        Args:
            phase: Trusted service fault phase; the client cannot supply this callback.
        """
        if phase=='before_purge:0':
            paths=list((state/AREA).glob('*/target-0'))
            path=paths[0]/'authored-after-validation.txt';path.write_text('preserve me');foreign.append(path)
    cleanup=MapCleanup(code,data,code.maintenance,fault=insert)
    try:
        plan=cleanup.preview([{'kind':'code','id':PROJECT}]);cleanup.execute(plan['planId'])
        final=wait_done(cleanup,plan['planId'])
        assert final['status']=='partial' and final['mapsCleared']
        assert foreign[0].read_text()=='preserve me'
        assert final['removedBytes'] is None
    finally:
        cleanup.close();data.close();code.close()
