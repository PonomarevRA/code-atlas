"""Missing current snapshots repair once; history and deliberately cleared maps remain read only."""
from concurrent.futures import Future
import json
import shutil
import pytest
from atlas.store import ProjectStore, StoreError
from atlas.snapshot_recovery import SnapshotRecoveryPending
from test_map_cleanup import publish_code, PROJECT


class Queue:
    """Keep a scheduled fixture build pending without executing analyzed code."""
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        """Record one scheduled build for later explicit completion by the test."""
        self.calls.append((function, args))
        return Future()


@pytest.fixture
def missing(tmp_path):
    """Publish then externally remove one synthetic snapshot, retaining its source/registration."""
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.py').write_text('def f(): return 1\n')
    store = ProjectStore(tmp_path / 'state')
    snapshot = publish_code(store, source)
    shutil.rmtree(snapshot)
    executor = store.executor
    store.executor = queue = Queue()
    yield store, queue, source
    executor.shutdown()


def test_current_graph_schedules_one_job_and_running_response_reuses_it(missing):
    """Polling current graph never duplicates a queued repair."""
    store, queue, _ = missing
    with pytest.raises(SnapshotRecoveryPending) as first:
        store.get_graph(PROJECT)
    assert first.value.code == 'SNAPSHOT_REBUILD_STARTED'
    job_id = first.value.job_id
    with pytest.raises(SnapshotRecoveryPending) as second:
        store.get_graph(PROJECT)
    assert second.value.code == 'SNAPSHOT_REBUILD_RUNNING'
    assert second.value.job_id == job_id and len(queue.calls) == 1


def test_failed_repair_marker_survives_restart_and_manual_rebuild_remains_possible(missing):
    """Failed/crashed automatic attempts are persistent; manual retry remains explicit."""
    store, queue, _ = missing
    with pytest.raises(SnapshotRecoveryPending):
        store.search_project_nodes(PROJECT, None, 'f')
    restarted = ProjectStore(store.data_dir)
    try:
        with pytest.raises(SnapshotRecoveryPending) as failure:
            restarted.get_graph(PROJECT)
        assert failure.value.code == 'SNAPSHOT_REBUILD_ATTEMPTED'
        store.active_job = None
        assert store.rebuild(PROJECT)['job']['id']
        assert len(queue.calls) == 2
    finally:
        restarted.executor.shutdown()


def test_executor_failure_keeps_attempt_but_releases_global_job(missing, monkeypatch):
    """A failed submission cannot trap the catalog in queued state or trigger automatic retry loops."""
    store, queue, _ = missing
    def reject(*args):
        """Simulate a stopped executor after the registry has received the queued job."""
        raise RuntimeError('executor unavailable')
    monkeypatch.setattr(queue, 'submit', reject)
    with pytest.raises(SnapshotRecoveryPending) as failure:
        store.get_graph(PROJECT)
    assert failure.value.code == 'SNAPSHOT_REBUILD_ATTEMPTED'
    assert store.active_job is None and store.projects[PROJECT]['status'] == 'failed'
    assert store.projects[PROJECT]['_missingSnapshotRepair']


@pytest.mark.parametrize('reader', ['pinned', 'status', 'background', 'cleared'])
def test_noncurrent_reads_and_explicit_cleanup_never_schedule(missing, reader):
    """Metadata, pinned/history and a deliberately cleared registration do not authorize rebuilding."""
    store, queue, _ = missing
    if reader == 'status':
        store.list_projects()
    elif reader == 'pinned':
        with pytest.raises(StoreError):
            store.search_project_nodes(PROJECT, 'a' * 32, 'f')
    elif reader == 'background':
        with pytest.raises(StoreError):
            store.comparison_snapshot(PROJECT)
    else:
        store.projects[PROJECT].pop('_graph')
        store.projects[PROJECT]['status'] = 'not-built'
        with pytest.raises(StoreError):
            store.get_graph(PROJECT)
    assert not queue.calls


@pytest.mark.parametrize('case', ['source-gone', 'source-link', 'source-parent-link', 'foreign-pointer', 'symlink-pointer', 'busy'])
def test_unsafe_unavailable_or_busy_scope_never_queues(missing, case, tmp_path):
    """Recovery never broadens storage/source authority or queues behind another active build."""
    store, queue, source = missing
    if case == 'source-gone':
        shutil.rmtree(source)
    elif case in {'source-link', 'source-parent-link'}:
        foreign = tmp_path / 'foreign'
        foreign.mkdir()
        if case == 'source-link':
            shutil.rmtree(source)
            source.symlink_to(foreign, target_is_directory=True)
        else:
            parent = tmp_path / 'redirected'
            parent.symlink_to(foreign, target_is_directory=True)
            (foreign / 'source').mkdir()
            store.projects[PROJECT]['path'] = str(parent / 'source')
    elif case == 'foreign-pointer':
        store.projects[PROJECT]['_graph'] = str(tmp_path / 'foreign' / ('a' * 32) / 'atlas-graph.json')
    elif case == 'symlink-pointer':
        parent = store.data_dir / 'maps/projects' / PROJECT
        shutil.rmtree(parent)
        parent.symlink_to(source, target_is_directory=True)
    else:
        store.active_job = 'other-job'
        store.jobs['other-job'] = {'projectId': 'other'}
    with pytest.raises(StoreError):
        store.get_graph(PROJECT)
    assert not queue.calls
