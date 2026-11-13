"""Deterministic ownership/expiry/drain tests, independent of extraction algorithms."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import shutil
from threading import Event
from types import SimpleNamespace
import time

import pytest

from atlas.checkout_maps import CheckoutMapError, CheckoutMaps
from atlas.checkout_maps.models import MARKER, MAX_SESSIONS, RECLAIM_IDLE_SECONDS, TTL_SECONDS
from atlas.checkout_maps import service


class Clocks:
    """Advance monotonic and UTC clocks together without sleeping an hour."""

    def __init__(self):
        self.monotonic, self.wall = 100.0, 1_800_000_000.0

    def advance(self, seconds):
        """Model elapsed inactivity; tests can alter wall separately for clock jumps."""
        self.monotonic += seconds
        self.wall += seconds


class FakeStore:
    """Controlled builder lifecycle; source parsing is exercised by root integration."""
    instances = []

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.path = self.data_dir / 'projects.json'
        self.projects = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.active_job, self.closed, self.adds, self.rebuilds = None, False, 0, 0
        self.instances.append(self)

    def add(self, checkout):
        """Capture source pointer and model one queued actual-store job."""
        self.adds += 1
        identifier = sha256(checkout.encode()).hexdigest()[:16]
        self.projects[identifier] = {'id': identifier, 'path': checkout, 'status': 'queued'}
        self.path.write_text(json.dumps(self.projects))
        self.active_job = 'building'
        return {'project': self.projects[identifier], 'job': {'id': 'building'}}

    def list_projects(self):
        """Return immutable status projections; never renew manager lifetime."""
        return {'projects': list(self.projects.values()), 'activeJob': {'id': self.active_job} if self.active_job else None}

    def rebuild(self, project_id):
        """Count a private rebuild so tests detect canonical/global dispatch mistakes."""
        self.rebuilds += 1
        self.active_job = 'rebuild'
        return {'job': {'id': self.active_job}}

    def complete(self):
        """Model a late builder finishing in its still-owned private directory."""
        assert self.data_dir.is_dir()
        (self.data_dir / 'snapshot.json').write_text('{"built":true}')
        self.active_job = None

    def close(self):
        """A drained fake store has no executor; keep its private status readable."""
        self.closed = True


@pytest.fixture
def setup(tmp_path):
    """One source fixture and independent app-data root with controlled clocks."""
    source, data = tmp_path / 'checkout', tmp_path / 'app'
    source.mkdir(); data.mkdir()
    (source / 'example.py').write_text('def useful():\n    return 1\n')
    clocks = Clocks()
    manager = CheckoutMaps(data, clock=lambda: clocks.monotonic, wall_clock=lambda: clocks.wall,
                           store_factory=FakeStore)
    try:
        yield manager, source, data, clocks
    finally:
        manager.close()


def handle(manager, identifier):
    """Inspect the internal fake builder only to arrange deterministic drain state."""
    return manager.entries[identifier].store


def test_separate_owners_private_stores_dedup_and_persistent_health(setup):
    """Same source is safe because all map/mod/cache state belongs to private roots."""
    manager, source, data, clock = setup
    (data / 'projects.json').write_text('{"canonical":true}')
    (data / 'mods.json').write_text('{"keep":true}')
    original_source = (source / 'example.py').read_bytes()
    first = manager.ensure(source, 'agent/task-1')
    clock.advance(500)
    assert manager.ensure(source, 'agent/task-1')['sessionId'] == first['sessionId']
    assert manager.status(first['sessionId'])['expiresAt'] == first['expiresAt']
    second = manager.ensure(source, 'agent/task-2')
    assert first['sessionId'] != second['sessionId']
    assert first['projectId'] == second['projectId']
    assert first['path'] != second['path'] and first['temporary']
    with manager.access(first['sessionId']) as entry:
        first_health = entry.health
        assert entry.health.store is entry.store
    with manager.access(first['sessionId']) as entry:
        assert entry.health is first_health
    assert (data / 'projects.json').read_text() == '{"canonical":true}'
    assert (data / 'mods.json').read_text() == '{"keep":true}'
    assert (source / 'example.py').read_bytes() == original_source
    assert handle(manager, first['sessionId']).adds == 1


def test_successful_use_slides_exact_hour_poll_and_failure_do_not(setup):
    """Only a successful still-live useful-read lease changes expiry."""
    manager, source, _, clock = setup
    created = manager.ensure(source, 'owner')
    identifier = created['sessionId']
    clock.advance(100)
    manager.list(); manager.status(identifier); manager.ensure(source, 'owner')
    assert manager.status(identifier)['expiresAt'] == created['expiresAt']
    with pytest.raises(RuntimeError):
        with manager.access(identifier):
            raise RuntimeError('read failed')
    assert manager.status(identifier)['expiresAt'] == created['expiresAt']
    with manager.access(identifier):
        clock.advance(10)
    renewed = manager.status(identifier)
    assert renewed['expiresAt'] != created['expiresAt']
    assert manager.entries[identifier].deadline == clock.monotonic+TTL_SECONDS


def test_expiry_rejects_even_inflight_use_and_never_revives(setup):
    """A long read cannot rescue a session after its fixed deadline has elapsed."""
    manager, source, _, clock = setup
    identifier = manager.ensure(source, 'owner')['sessionId']
    store = handle(manager, identifier); store.complete()
    with pytest.raises(CheckoutMapError) as error:
        with manager.access(identifier):
            clock.advance(TTL_SECONDS)
            assert Path(manager.entries[identifier].path).exists()
    assert error.value.status == 410
    assert not store.data_dir.exists()
    with pytest.raises(CheckoutMapError):
        with manager.access(identifier):
            pass
    assert manager.ensure(source, 'owner')['sessionId'] != identifier


def test_finish_during_read_retires_then_waits_for_lease(setup):
    """Retirement immediately rejects new work but cannot delete the active reader."""
    manager, source, _, _ = setup
    identifier = manager.ensure(source, 'owner')['sessionId']
    store = handle(manager, identifier); store.complete()
    with pytest.raises(CheckoutMapError) as error:
        with manager.access(identifier):
            result = manager.finish(identifier)
            assert result['state'] == 'deleting' and store.data_dir.exists()
            with pytest.raises(CheckoutMapError):
                with manager.access(identifier):
                    pass
    assert error.value.status == 410
    assert not store.data_dir.exists()


def test_concurrent_finish_and_use_cannot_remove_pinned_reader(setup):
    """Two actual threads exercise admission/retirement under the service lock."""
    manager, source, _, _ = setup
    identifier = manager.ensure(source, 'owner')['sessionId']; store = handle(manager, identifier); store.complete()
    entered, release = Event(), Event()
    def read():
        """Hold a useful-read lease until the separate finishing thread retires it."""
        with manager.access(identifier):
            entered.set()
            assert release.wait(3)
            assert store.data_dir.exists()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read)
        try:
            assert entered.wait(3)
            assert manager.finish(identifier)['state'] == 'deleting'
            assert store.data_dir.exists()
        finally:
            release.set()
        with pytest.raises(CheckoutMapError) as error:
            future.result(timeout=3)
        assert error.value.status == 410
    assert not store.data_dir.exists()


@pytest.mark.parametrize('retirement', ['finish', 'expiry'])
def test_late_builder_keeps_owned_folder_until_drain(setup, retirement):
    """A retired build can finish privately without recreating a deleted session."""
    manager, source, _, clock = setup
    identifier = manager.ensure(source, 'owner')['sessionId']
    store = handle(manager, identifier)
    if retirement == 'finish':
        assert manager.finish(identifier)['state'] == 'deleting'
    else:
        clock.advance(TTL_SECONDS); manager.sweep()
    assert store.data_dir.exists() and not store.closed
    with pytest.raises(CheckoutMapError) as error:
        with manager.access(identifier):
            pass
    assert error.value.status == 410
    assert manager.list()['sessions'][0]['state'] == 'deleting'
    store.complete()
    manager.sweep()
    assert store.closed and not store.data_dir.exists()
    assert manager.list()['sessions'] == []


def test_capacity_includes_retiring_without_eviction(setup):
    """Active and pending-builder sessions both consume the bounded store capacity."""
    manager, source, _, _ = setup
    identifiers = [manager.ensure(source, 'owner-'+str(index))['sessionId'] for index in range(MAX_SESSIONS)]
    assert MAX_SESSIONS == 8
    assert manager.ensure(source, 'owner-1')['sessionId'] == identifiers[1]
    manager.finish(identifiers[0])
    with pytest.raises(CheckoutMapError) as error:
        manager.ensure(source, 'new-owner')
    assert error.value.status == 429
    assert error.value.code == 'checkout_map_capacity'
    assert 'list_checkout_maps' in str(error.value)
    assert {row['sessionId'] for row in manager.list()['sessions']} == set(identifiers)
    handle(manager, identifiers[0]).complete(); manager.sweep()
    assert manager.ensure(source, 'new-owner')['sessionId'] not in identifiers


def test_reclaim_respects_activity_and_retries_reuse_the_new_map(setup):
    """Capacity recovery uses idle time, skips readers/builders and removes just one map."""
    manager, source, _, clock = setup
    manager.capacity = 5
    building = manager.ensure(source, 'building')
    clock.advance(1)
    reading = manager.ensure(source, 'reading')
    clock.advance(1)
    oldest = manager.ensure(source, 'oldest')
    clock.advance(1)
    later = manager.ensure(source, 'later')
    clock.advance(1)
    renewed = manager.ensure(source, 'renewed')
    for session in (reading, oldest, later, renewed):
        handle(manager, session['sessionId']).complete()
    clock.advance(RECLAIM_IDLE_SECONDS)
    with manager.access(renewed['sessionId']):
        pass
    with manager.access(reading['sessionId']):
        assert not manager.status(reading['sessionId'])['reclaim']['eligible']
        assert not manager.status(building['sessionId'])['reclaim']['eligible']
        assert manager.status(oldest['sessionId'])['reclaim']['eligible']
        current = manager.ensure(source, 'current', reclaim_idle=True)
        assert current['reclaimed'] == {'sessionId': oldest['sessionId'], 'idleSeconds': RECLAIM_IDLE_SECONDS + 2}
        assert manager.ensure(source, 'current', reclaim_idle=True)['sessionId'] == current['sessionId']
    assert {row['sessionId'] for row in manager.list()['sessions']} == {
        session['sessionId'] for session in (building, reading, later, renewed, current)
    }
    assert not Path(oldest['path']).exists()
    assert (source / 'example.py').is_file()


def test_reclaim_requires_capacity_and_minimum_idle_time(setup):
    """A vacant slot avoids eviction, and a recently used map keeps its slot."""
    manager, source, _, clock = setup
    manager.capacity = 2
    old = manager.ensure(source, 'old')
    handle(manager, old['sessionId']).complete()
    clock.advance(RECLAIM_IDLE_SECONDS)
    recent = manager.ensure(source, 'recent', reclaim_idle=True)
    assert 'reclaimed' not in recent
    handle(manager, recent['sessionId']).complete()
    with manager.access(old['sessionId']):
        pass
    with pytest.raises(CheckoutMapError) as error:
        manager.ensure(source, 'new', reclaim_idle=True)
    assert error.value.status == 429
    assert {row['sessionId'] for row in manager.list()['sessions']} == {old['sessionId'], recent['sessionId']}


def test_concurrent_reclaim_for_one_task_removes_only_one_map(setup):
    """Concurrent retries for the same task share the admitted session."""
    manager, source, _, clock = setup
    manager.capacity = 2
    oldest = manager.ensure(source, 'oldest')
    clock.advance(1)
    other = manager.ensure(source, 'other')
    for session in (oldest, other):
        handle(manager, session['sessionId']).complete()
    clock.advance(RECLAIM_IDLE_SECONDS)
    with ThreadPoolExecutor(max_workers=2) as executor:
        retries = [executor.submit(manager.ensure, source, 'current', reclaim_idle=True) for _ in range(2)]
        results = [retry.result() for retry in retries]
    assert results[0]['sessionId'] == results[1]['sessionId']
    assert [result['reclaimed']['sessionId'] for result in results if 'reclaimed' in result] == [oldest['sessionId']]
    assert Path(other['path']).is_dir()


def test_restart_after_reclaim_folder_removed_before_index_update(setup):
    """Recover an interrupted eviction while preserving the remaining private catalog."""
    manager, source, data, clock = setup
    old = manager.ensure(source, 'old')
    other = manager.ensure(source, 'other')
    manager.close()
    index = json.loads(manager.index.read_text())
    index['sessions'][0].update(state='deleting', retiredReason='capacity')
    manager.index.write_text(json.dumps(index))
    shutil.rmtree(old['path'])
    restored = CheckoutMaps(data, clock=lambda: clock.monotonic, wall_clock=lambda: clock.wall, store_factory=FakeStore)
    try:
        assert [row['sessionId'] for row in restored.list()['sessions']] == [other['sessionId']]
    finally:
        restored.close()


@pytest.mark.parametrize(('platform', 'clock_id'), [('darwin', 4), ('linux', 7)])
def test_system_sleep_expires_temporary_maps(tmp_path, monkeypatch, platform, clock_id):
    """Platform elapsed clocks retire idle maps after suspend, including at capacity."""
    clocks = Clocks()
    elapsed = clocks.monotonic
    def read_clock(requested):
        assert requested == clock_id
        return elapsed
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(service, 'time', SimpleNamespace(
        CLOCK_MONOTONIC_RAW=4, CLOCK_BOOTTIME=7, clock_gettime=read_clock,
        monotonic=lambda: clocks.monotonic,
    ))
    source = tmp_path / 'checkout'
    source.mkdir()
    manager = CheckoutMaps(tmp_path / 'app', wall_clock=lambda: clocks.wall,
                           capacity=1, store_factory=FakeStore)
    try:
        old = manager.ensure(source, 'old')
        handle(manager, old['sessionId']).complete()
        elapsed += TTL_SECONDS
        clocks.wall += TTL_SECONDS
        new = manager.ensure(source, 'new')
        assert new['sessionId'] != old['sessionId']
        assert not Path(old['path']).exists()
    finally:
        manager.close()


def test_shutdown_preserves_live_index_restart_expires_known_only(setup):
    """Restart honors persisted expiry, leaving all unknown folders untouched."""
    manager, source, data, clock = setup
    identifier = manager.ensure(source, 'owner')['sessionId']
    handle(manager, identifier).complete()
    unknown = manager.root / 'unknown'; unknown.mkdir(); (unknown / 'keep').write_text('preserve')
    manager.close()
    assert (manager.root / identifier).exists()
    restarted = CheckoutMaps(data, clock=lambda: clock.monotonic, wall_clock=lambda: clock.wall, store_factory=FakeStore)
    try:
        assert restarted.status(identifier)['sessionId'] == identifier
        assert restarted.ensure(source, 'owner')['sessionId'] == identifier
        clock.advance(TTL_SECONDS)
    finally:
        restarted.close()
    again = CheckoutMaps(data, clock=lambda: clock.monotonic, wall_clock=lambda: clock.wall, store_factory=FakeStore)
    try:
        assert again.list()['sessions'] == []
        assert not (again.root / identifier).exists()
        assert (unknown / 'keep').read_text() == 'preserve'
    finally:
        again.close()


def test_restart_at_expiry_and_backwards_wallclock_are_bounded(setup):
    """Live expiry is monotonic; restoration never grants more than a fresh hour."""
    manager, source, data, clock = setup
    identifier = manager.ensure(source, 'owner')['sessionId']; handle(manager, identifier).complete()
    clock.wall -= 100000
    clock.monotonic += TTL_SECONDS
    manager.sweep()
    assert not (manager.root / identifier).exists()
    manager.close()
    restored = CheckoutMaps(data, clock=lambda: clock.monotonic, wall_clock=lambda: clock.wall, store_factory=FakeStore)
    try:
        assert restored.list()['sessions'] == []
    finally:
        restored.close()


@pytest.mark.parametrize('unsafe', ['folder', 'marker', 'nested'])
def test_symlinks_never_delete_victim_cleanup_failure_visible(setup, tmp_path, unsafe):
    """Owned marker checks plus ordinary-tree guard prevent link-following cleanup."""
    manager, source, _, _ = setup
    identifier = manager.ensure(source, 'owner')['sessionId']; store = handle(manager, identifier); store.complete()
    victim = tmp_path / 'victim'; victim.mkdir(); (victim / 'keep').write_text('untouched')
    path = store.data_dir
    if unsafe == 'folder':
        parked = path.with_name(path.name+'-parked'); path.rename(parked); path.symlink_to(victim)
    elif unsafe == 'marker':
        marker = path / MARKER; original = marker.read_text(); marker.unlink()
        target = victim / 'marker'; target.write_text(original); marker.symlink_to(target)
    else:
        (path / 'external').symlink_to(victim)
    result = manager.finish(identifier)
    assert result['state'] == 'cleanup-failed' and result['cleanupError']
    assert (victim / 'keep').read_text() == 'untouched'
    assert manager.list()['sessions'][0]['state'] == 'cleanup-failed'


def test_cleanup_failure_retry_is_owned_and_visible(setup, monkeypatch):
    """A transient delete error retains the entry and allows a later bounded retry."""
    import atlas.checkout_maps.service as service
    manager, source, _, _ = setup
    identifier = manager.ensure(source, 'owner')['sessionId']; handle(manager, identifier).complete()
    real = service.shutil.rmtree
    def failed(path):
        """Model a filesystem refusal without deleting any directory."""
        raise OSError('busy')
    monkeypatch.setattr(service.shutil, 'rmtree', failed)
    assert manager.finish(identifier)['state'] == 'cleanup-failed'
    monkeypatch.setattr(service.shutil, 'rmtree', real)
    manager.sweep()
    assert manager.list()['sessions'] == []


def test_rebuild_runtime_selection_and_unknown_ids(setup):
    """Rebuild selects the private owner; unknown IDs never form disk paths."""
    manager, source, _, _ = setup
    manager.runtime_python = '/captured/python'
    created = manager.ensure(source, 'owner')
    store = handle(manager, created['sessionId'])
    assert store.graphify_runtime.selected() == '/captured/python'
    assert created['runtimeSelection']['python'] == '/captured/python'
    assert manager.rebuild(created['sessionId'])['job']['id'] == 'rebuild'
    assert store.rebuilds == 1
    for identifier in ('../checkout', '/', 'missing'):
        with pytest.raises(CheckoutMapError) as error:
            with manager.access(identifier):
                pass
        assert error.value.status == 410


def test_invalid_storage_index_and_unmarked_folder_fail_closed(tmp_path):
    """A syntactically valid UUID alone never authorizes opening or deleting a folder."""
    root = tmp_path / 'app'; root.mkdir()
    manager = CheckoutMaps(root, store_factory=FakeStore)
    folder = manager.root / ('a'*32); folder.mkdir(); (folder / 'keep').write_text('unknown')
    manager.index.write_text(json.dumps({'schemaVersion': 'atlas-checkout-maps/v1', 'sessions': [{'sessionId': 'a'*32}]}))
    with pytest.raises(CheckoutMapError):
        CheckoutMaps(root, store_factory=FakeStore)
    assert (folder / 'keep').read_text() == 'unknown'


@pytest.mark.parametrize('replacement', ['folder', 'marker'])
def test_ensure_dedup_rechecks_existing_owned_folder(setup, tmp_path, replacement):
    """A replaced session path/marker is not reported as a reusable live checkout."""
    manager, source, _, _ = setup
    created = manager.ensure(source, 'owner')
    identifier = created['sessionId']; store = handle(manager, identifier); store.complete()
    victim = tmp_path / 'other'; victim.mkdir(); (victim / 'keep').write_text('preserved')
    folder = store.data_dir
    if replacement == 'folder':
        parked = folder.with_name(folder.name+'-parked'); folder.rename(parked); folder.symlink_to(victim)
    else:
        marker = folder / MARKER; marker.write_text('{"different":"owner"}')
    with pytest.raises(CheckoutMapError) as error:
        manager.ensure(source, 'owner')
    assert error.value.status == 403
    assert store.adds == 1
    assert (victim / 'keep').read_text() == 'preserved'


def test_replaced_storage_root_blocks_allocation_without_writing_victim(setup, tmp_path):
    """A root symlink introduced after construction cannot redirect a fresh store."""
    manager, source, _, _ = setup
    victim = tmp_path / 'victim-root'; victim.mkdir(); (victim / 'keep').write_text('preserved')
    parked = manager.root.with_name(manager.root.name+'-parked')
    manager.root.rename(parked); manager.root.symlink_to(victim)
    try:
        with pytest.raises(CheckoutMapError) as error:
            manager.ensure(source, 'owner')
        assert error.value.status == 403
        assert sorted(path.name for path in victim.iterdir()) == ['keep']
        assert manager.entries == {}
    finally:
        manager.root.unlink(); parked.rename(manager.root)


@pytest.mark.parametrize('existing_empty_index', [False, True])
def test_unused_namespace_sweep_and_shutdown_do_not_create_late_index(tmp_path, existing_empty_index):
    """An unused optional feature stays disk-idle; an existing empty index remains valid."""
    data = tmp_path / 'data'; data.mkdir()
    root = data / 'maps/temporary-checkouts'; root.mkdir(parents=True)
    index = root / 'index.json'
    if existing_empty_index:
        index.write_text(json.dumps({'schemaVersion': 'atlas-checkout-maps/v1', 'sessions': []}))
    manager = CheckoutMaps(data, store_factory=FakeStore)
    before = set(data.rglob('*'))
    manager.start(); manager.sweep()
    assert manager.list()['sessions'] == []
    manager.close()
    assert set(data.rglob('*')) == before
    assert index.exists() is existing_empty_index
    if existing_empty_index:
        assert json.loads(index.read_text())['sessions'] == []


@pytest.mark.parametrize('field', ['createdAtEpoch', 'expiresAtEpoch'])
@pytest.mark.parametrize('value', [1e100, -1e100, 10**500, -(10**500)])
def test_restore_rejects_unrepresentable_utc_before_open_or_cleanup(setup, field, value):
    """CK1: finite but unrenderable dates cannot restore an apparently usable namespace."""
    manager, source, data, clocks = setup
    identifier = manager.ensure(source, 'owner')['sessionId']; handle(manager, identifier).complete()
    manager.close()
    index = manager.index
    record = json.loads(index.read_text())
    record['sessions'][0][field] = value
    index.write_text(json.dumps(record))
    original_index, original_source = index.read_bytes(), (source / 'example.py').read_bytes()
    folders_before = set(manager.root.rglob('*'))
    opened = len(FakeStore.instances)
    with pytest.raises(CheckoutMapError) as error:
        CheckoutMaps(data, clock=lambda: clocks.monotonic, wall_clock=lambda: clocks.wall, store_factory=FakeStore)
    assert error.value.status == 503 and error.value.code == 'checkout_map_storage'
    assert len(FakeStore.instances) == opened
    assert index.read_bytes() == original_index
    assert set(manager.root.rglob('*')) == folders_before
    assert (source / 'example.py').read_bytes() == original_source


def test_actual_project_store_builder_drains_before_private_folder_delete(tmp_path, monkeypatch):
    """Reuse real ProjectStore/executor/snapshot publication with a controlled provider."""
    from atlas import store as store_module
    began, release = Event(), Event()
    source, data = tmp_path / 'source', tmp_path / 'data'
    source.mkdir(); data.mkdir()
    original = 'def first():\n    return 1\n'
    (source / 'service.py').write_text(original)
    (data / 'projects.json').write_text('{"canonical":true}')
    def extracted(checkout, output):
        """Write a minimal raw fixture only to the real store's owned output path."""
        began.set()
        assert release.wait(5)
        raw_path = output / 'graphify-out/graph.json'; raw_path.parent.mkdir(parents=True)
        raw = {'nodes': [{'id': 'first', 'label': 'first', 'kind': 'function', 'file': 'service.py'}], 'edges': []}
        raw_path.write_text(json.dumps(raw))
        return {**raw, 'metadata': {'rawGraphPath': str(raw_path), 'nodeCount': 1, 'edgeCount': 0}}
    monkeypatch.setattr(store_module, 'build_graph', extracted)
    manager = CheckoutMaps(data)
    try:
        created = manager.ensure(source, 'actual-store')
        identifier = created['sessionId']; folder = Path(created['path'])
        assert began.wait(5)
        assert manager.finish(identifier)['state'] == 'deleting'
        assert folder.is_dir()
        release.set()
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            manager.sweep()
            if not folder.exists():
                break
            time.sleep(0.01)
        assert not folder.exists()
        assert manager.list()['sessions'] == []
        assert (source / 'service.py').read_text() == original
        assert (data / 'projects.json').read_text() == '{"canonical":true}'
    finally:
        release.set()
        manager.close()
