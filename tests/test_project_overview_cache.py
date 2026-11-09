"""Overview hydration cannot monopolize the project registry or retain entire graphs."""

from concurrent.futures import ThreadPoolExecutor
import gc
from threading import Event
import weakref

import pytest

from atlas.store import ProjectStore, StoreError
from atlas.snapshot_objects import write_snapshot
from test_map_cleanup import publish_code, PROJECT, SNAPSHOT


@pytest.fixture
def overview_store(tmp_path):
    """
    Publish an isolated canonical snapshot, without scheduling any background build.

    Args:
        tmp_path: Temporary source and state roots unrelated to registered user maps.
    Yields:
        Store, source directory and canonical snapshot path; executor is always closed.
    """
    source = tmp_path / 'source'
    source.mkdir()
    store = ProjectStore(tmp_path / 'state')
    directory = publish_code(store, source)
    store.jobs['job'] = {'id': 'job', 'projectId': PROJECT, 'stage': 'captured', 'status': 'completed'}
    try:
        yield store, source, directory / 'atlas-graph.json'
    finally:
        store.close()


def test_blocked_cold_read_allows_registry_and_job_status(overview_store, monkeypatch):
    """
    A cold overview waits outside store.lock while unrelated lightweight reads complete.

    Args:
        overview_store: Isolated published map fixture.
        monkeypatch: Installs an event-controlled canonical reader for this test only.
    """
    store, _, _ = overview_store
    entered, release = Event(), Event()
    original = store._read_graph

    def blocked(path):
        """
        Hold only this fixture read until registry liveness has been checked.

        Args:
            path: Safe isolated canonical fixture path.
        """
        assert not store.lock._is_owned()
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(store, '_read_graph', blocked)
    with ThreadPoolExecutor(max_workers=3) as executor:
        overview = executor.submit(store.project_overview, PROJECT, SNAPSHOT)
        try:
            assert entered.wait(2)
            assert executor.submit(store.get_job, 'job').result(timeout=1)['id'] == 'job'
            assert executor.submit(store.list_projects).result(timeout=1)['projects'][0]['id'] == PROJECT
        finally:
            release.set()
        assert overview.result(timeout=3)['snapshot']['available']


def test_warm_overview_ignores_health_overlay_and_refreshes_dynamic_state(overview_store, monkeypatch):
    """
    Static evidence is reused without graph hydration; status/stale/job fields remain current.

    Args:
        overview_store: Isolated canonical snapshot fixture.
        monkeypatch: Makes accidental graph/base-cache reads fail visibly.
    """
    store, _, path = overview_store
    first = store.project_overview(PROJECT)

    def forbidden(*args):
        """
        A warm overview cannot depend on full-graph or derived-health hydration.

        Args:
            *args: Unchanged fixture arguments.
        """
        raise AssertionError('Unexpected full graph hydration')

    monkeypatch.setattr(store, '_read_graph', forbidden)
    monkeypatch.setattr(store, '_base_snapshot', forbidden)
    path.with_name('code-health-overlay.json').write_text('{"changed":true}')
    with store.lock:
        store.projects[PROJECT]['status'] = 'failed'
        store.jobs['job']['stage'] = 'new-stage'
    result = store.project_overview(PROJECT)
    assert result['areas'] == first['areas']
    assert result['state'] == 'build_failed_with_snapshot' and result['snapshot']['stale']
    assert result['build']['stage'] == 'new-stage'
    result['areas'].clear()
    assert store.project_overview(PROJECT)['areas'] == first['areas']


def test_two_cold_callers_share_one_hydration_without_holding_registry(overview_store, monkeypatch):
    """
    Waiting for another overview never holds the registry lock and does not duplicate the cold graph read.

    Args:
        overview_store: Isolated current snapshot and job fixture.
        monkeypatch: Pauses the first canonical hydration until both callers are queued.
    """
    store, _, _ = overview_store
    entered, release = Event(), Event()
    calls, original = [], store._read_graph

    def blocked(path):
        """
        Hold a single fixture hydration while independent status reads continue.

        Args:
            path: Safe canonical fixture path selected by the store.
        Returns:
            Original decoded graph after explicit release by the test.
        """
        calls.append(path)
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(store, '_read_graph', blocked)
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(store.project_overview, PROJECT)
        try:
            assert entered.wait(2)
            second = executor.submit(store.project_overview, PROJECT)
            assert executor.submit(store.get_job, 'job').result(timeout=1)['id'] == 'job'
        finally:
            release.set()
        assert first.result(timeout=3) == second.result(timeout=3)
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['snapshot', 'source'])
def test_cold_overview_rechecks_snapshot_and_source(overview_store, monkeypatch, change):
    """
    A revision or canonical source mutation during hydration rejects the old evidence with 409.

    Args:
        overview_store: Isolated published map fixture.
        monkeypatch: Pauses after reading the original canonical graph.
        change: Switch the registry snapshot or replace same-SID canonical content.
    """
    store, source, path = overview_store
    entered, release = Event(), Event()
    original = store._read_graph

    def blocked(current):
        """
        Return the first decoded graph only after the test changes its identity.

        Args:
            current: Safe isolated canonical fixture path.
        """
        graph = original(current)
        entered.set()
        assert release.wait(5)
        return graph

    monkeypatch.setattr(store, '_read_graph', blocked)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(store.project_overview, PROJECT, SNAPSHOT)
        try:
            assert entered.wait(2)
            if change == 'snapshot':
                publish_code(store, source, snapshot='b' * 32)
            else:
                write_snapshot(path, {'nodes': [{'id': 'changed', 'file': 'new/a.py'}],
                                      'edges': [], 'metadata': {}})
        finally:
            release.set()
        with pytest.raises(StoreError) as error:
            pending.result(timeout=3)
        assert error.value.status == 409


def test_warm_source_change_invalidates_only_that_evidence(overview_store, monkeypatch):
    """
    A canonical object change with the same SID cannot reuse stale area paths.

    Args:
        overview_store: Isolated snapshot fixture.
        monkeypatch: Counts actual canonical graph reads.
    """
    store, _, path = overview_store
    calls, original = [], store._read_graph

    def counted(current):
        """
        Count canonical hydration rather than inexpensive source identity checks.

        Args:
            current: Safe isolated canonical fixture path.
        """
        calls.append(current)
        return original(current)

    monkeypatch.setattr(store, '_read_graph', counted)
    store.project_overview(PROJECT)
    write_snapshot(path, {'nodes': [{'id': 'new', 'file': 'changed/a.py'}], 'edges': [], 'metadata': {}})
    result = store.project_overview(PROJECT)
    assert len(calls) == 2 and result['areas'][0]['path'] == 'changed'


def test_overview_cache_retains_only_bounded_dto(overview_store, monkeypatch):
    """
    Cache entries do not hold a full decoded graph and are bounded independently of graph LRU.

    Args:
        overview_store: Isolated canonical map fixture.
        monkeypatch: Supplies a weak-referenceable decoded graph with irrelevant heavy payload.
    """
    store, source, _ = overview_store
    other = '2' * 16
    publish_code(store, source, project_id=other)
    references = []

    class Graph(dict):
        """Expose object lifetime without altering the normal JSON graph interface."""

    def read_graph(path):
        """
        Supply a large decoded payload that is irrelevant to the static overview DTO.

        Args:
            path: Safe isolated canonical fixture path.
        """
        graph = Graph(nodes=[{'id': 'n', 'file': 'a.py', 'unrelated': 'x' * (120 * 1024 * 1024)}], metadata={})
        references.append(weakref.ref(graph))
        return graph

    monkeypatch.setattr(store, '_read_graph', read_graph)
    store.project_overview(PROJECT)
    store.project_overview(other)
    gc.collect()
    assert all(reference() is None for reference in references)
    assert len(store._overview_cache) == 2 and store._overview_cache_bytes < 10000
    assert not store._published_graph


def test_cache_enforces_context_and_byte_quotas(overview_store, monkeypatch):
    """
    Distinct revisions cannot grow the overview cache beyond its entry or accounted byte budget.

    Args:
        overview_store: Isolated source and registry.
        monkeypatch: Lowers only fixture quotas to exercise both eviction paths.
    """
    import atlas.store as module
    store, source, _ = overview_store
    monkeypatch.setattr(module, 'MAX_OVERVIEW_CONTEXTS', 2)
    for number in range(3):
        identifier = f'{number + 2:016x}'
        publish_code(store, source, project_id=identifier)
        store.project_overview(identifier)
    assert len(store._overview_cache) == 2
    assert store._overview_cache_bytes == sum(size for _, size in store._overview_cache.values())
    one_size = next(iter(store._overview_cache.values()))[1]
    monkeypatch.setattr(module, 'MAX_OVERVIEW_CACHE_BYTES', one_size + 10)
    store.project_overview(PROJECT)
    assert len(store._overview_cache) == 1 and store._overview_cache_bytes <= one_size + 10


def test_single_oversized_dto_is_refused_without_cache_growth(overview_store, monkeypatch):
    """
    A single excessive evidence DTO is explicit refusal rather than an unbounded retained entry.

    Args:
        overview_store: Isolated published fixture.
        monkeypatch: Lowers the cache byte cap solely to test the refusal branch.
    """
    import atlas.store as module
    store, _, _ = overview_store
    monkeypatch.setattr(module, 'MAX_OVERVIEW_CACHE_BYTES', 1)
    with pytest.raises(StoreError) as error:
        store.project_overview(PROJECT)
    assert error.value.status == 413
    assert not store._overview_cache and store._overview_cache_bytes == 0


def test_source_change_while_waiting_for_final_registry_pin_is_rejected(overview_store, monkeypatch):
    """
    A source mutation at the last registry check cannot publish the already captured old DTO.

    Args:
        overview_store: Isolated canonical fixture.
        monkeypatch: Mutates only this fixture's source after the final metadata pin.
    """
    store, _, path = overview_store
    original, calls = store._overview_scope, []

    def pin(*args):
        """
        Replace the fixture source exactly once after its second registry pin.

        Args:
            *args: Unchanged fixture arguments.
        """
        scope = original(*args)
        calls.append(scope)
        if len(calls) == 2:
            write_snapshot(path, {'nodes': [{'id': 'different', 'file': 'new.py'}], 'edges': [], 'metadata': {}})
        return scope

    monkeypatch.setattr(store, '_overview_scope', pin)
    with pytest.raises(StoreError) as error:
        store.project_overview(PROJECT)
    assert error.value.status == 409 and not store._overview_cache


def test_no_snapshot_overview_does_not_hydrate_or_build(overview_store, monkeypatch):
    """
    Missing snapshots keep their explicit overview state and never create a rebuild job.

    Args:
        overview_store: Isolated registration with its snapshot pointer removed.
        monkeypatch: Makes unintended hydration/build scheduling fail.
    """
    store, _, _ = overview_store
    with store.lock:
        store.projects[PROJECT].pop('_graph')
        store.projects[PROJECT]['status'] = 'not-built'

    def forbidden(*args):
        """
        A metadata-only overview must neither read a graph nor schedule extraction.

        Args:
            *args: Unchanged fixture arguments.
        """
        raise AssertionError('Unexpected hydration/build')

    monkeypatch.setattr(store, '_read_graph', forbidden)
    monkeypatch.setattr(store, '_submit', forbidden)
    result = store.project_overview(PROJECT)
    assert result['state'] == 'not_built' and not result['snapshot']['available']
