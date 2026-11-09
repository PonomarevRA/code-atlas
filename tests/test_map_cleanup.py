"""
Synthetic map cleanup ownership, rollback, replay and restart contracts; no real user map is touched.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
import pytest

from atlas.store import ProjectStore, write_json, StoreError
from atlas.data_store import DataMapStore
from atlas.data_models import DataInput, DataError
from atlas.snapshot_objects import write_snapshot, read_snapshot
from atlas.map_cleanup import MapCleanup
from atlas.map_cleanup_inventory import CleanupError
from atlas.map_cleanup_journal import recover, AREA

PROJECT = '1' * 16
OTHER = '2' * 16
SNAPSHOT = 'a' * 32


def wait_done(cleanup, plan_id):
    """
    Wait for the synthetic quarantine worker without replaying confirmation.

    Args:
        cleanup: Fixture service whose own executor is closed by the caller.
        plan_id: Existing explicit confirmed operation.
    Returns:
        Terminal public operation; test fails on an unexpected worker hang.
    """
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = cleanup.status(plan_id)
        if value['status'] != 'purging':
            return value
        time.sleep(.005)
    raise AssertionError('Cleanup fixture did not reach a terminal state')


def publish_code(store, source, project_id=PROJECT, snapshot=SNAPSHOT, layout='canonical'):
    """
    Publish a tiny real CAS-backed fixture under a selected supported layout.

    Args:
        store: Isolated ProjectStore owning the test data root.
        source: Separate source repository directory, never a cleanup target.
        project_id: Registered fixture ID, used only under server-derived layouts.
        snapshot:32-character fixture snapshot identity.
        layout: canonical, projects or legacy storage root selector.
    Returns:
        Complete snapshot directory containing a real manifest and raw vendor fixture.
    """
    prefix = {'canonical': ('maps', 'projects'), 'projects': ('projects',), 'legacy': ()}[layout]
    directory = store.data_dir.joinpath(*prefix, project_id, snapshot)
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / 'graphify-out' / 'graph.json'
    raw.parent.mkdir()
    raw.write_text('{"nodes":[],"edges":[]}')
    graph = {'nodes': [{'id': 'n', 'file': 'a.py', 'label': 'f', 'kind': 'function'}], 'edges': [],
             'metadata': {'snapshotId': snapshot, 'rawGraphPath': str(raw)}}
    write_snapshot(directory / 'atlas-graph.json', graph)
    store.projects[project_id] = {'id': project_id, 'name': 'Fixture', 'path': str(source),
        'status': 'completed', 'nodeCount': 1, 'edgeCount': 0, '_graph': str(directory / 'atlas-graph.json'),
        '_rawGraph': str(raw), '_mcpGraph': str(directory / 'mcp-graph.json'), '_mcpGraphLazy': True}
    write_json(store.registry, store.projects)
    return directory


@pytest.fixture
def fixture(tmp_path):
    """
    Create independent code/source/data roots with lifecycle-owned test services.

    Args:
        tmp_path: Pytest private directory; production registries and maps are never opened.
    Yields:
        Code store, data store, cleanup service, source root and current snapshot directory.
    """
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.py').write_text('def f():\n return 1\n')
    code = ProjectStore(tmp_path / 'state')
    data = DataMapStore(code.data_dir, maintenance=code.maintenance)
    directory = publish_code(code, source)
    cleanup = MapCleanup(code, data, code.maintenance)
    yield code, data, cleanup, source, directory
    cleanup.close()
    data.close()
    code.close()


def test_clear_code_preserves_registration_sources_views_and_other_project_cas(fixture):
    """
    Clear all selected layouts/CAS without broad project-root removal or foreign object deletion.

    Args:
        fixture: Isolated code and source scopes with a real CAS-backed current snapshot.
    """
    code, data, cleanup, source, current = fixture
    other_source = source.parent / 'other'; other_source.mkdir()
    (other_source / 'a.py').write_text('def other(): pass')
    other = publish_code(code, other_source, OTHER)
    for layout, snapshot in [('legacy', 'b' * 32), ('projects', 'c' * 32)]:
        publish_code(code, source, PROJECT, snapshot, layout)
    # Restore canonical current registration while keeping historical layouts.
    code.projects[PROJECT]['_graph'] = str(current / 'atlas-graph.json')
    code.projects[PROJECT]['_rawGraph'] = str(current / 'graphify-out/graph.json')
    code.projects[PROJECT]['_mcpGraph'] = str(current / 'mcp-graph.json')
    write_json(code.registry, code.projects)
    parent = current.parent
    (parent / 'views').mkdir(); (parent / 'views/authored.json').write_text('authored')
    (parent / 'personal.txt').write_text('preserve unknown nonmap file')
    protected = {p: p.read_bytes() for p in other.parent.rglob('*') if p.is_file()}
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    assert plan['maps'][0]['snapshotCount'] == 3 and plan['totals']['sizeBytes'] > 0
    assert plan['maps'][0]['versionStatus'] == 'unknown'
    assert current.exists(), 'Preview alone must never change files'
    receipt = cleanup.execute(plan['planId'])
    final = wait_done(cleanup, plan['planId'])
    assert final['status'] == 'completed' and final['mapsCleared'] is True
    assert final['removedBytes'] == plan['totals']['sizeBytes']
    assert code.projects[PROJECT]['status'] == 'not-built'
    assert code.projects[PROJECT]['nodeCount'] == code.projects[PROJECT]['edgeCount'] == 0
    assert not any(k in code.projects[PROJECT] for k in ['_graph', '_rawGraph', '_mcpGraph'])
    assert (parent / 'views/authored.json').read_text() == 'authored'
    assert (parent / 'personal.txt').exists() and (source / 'a.py').exists()
    assert not current.exists() and not (parent / '_objects').exists()
    assert all(p.read_bytes() == content for p, content in protected.items())
    assert read_snapshot(other / 'atlas-graph.json')['metadata']['snapshotId'] == SNAPSHOT
    assert cleanup.execute(plan['planId']) == final
    with pytest.raises(StoreError):
        code.comparison_snapshot(PROJECT)


@pytest.mark.parametrize('mutation', ['bytes', 'snapshot', 'object', 'unknown', 'link', 'catalog'])
def test_new_inventory_or_catalog_after_preview_never_mutates(fixture, mutation):
    """
    Any changed selected identity causes stale/unsafe rejection before the first rename.

    Args:
        fixture: Isolated published map and cleanup service.
        mutation: Synthetic file/catalog/ownership change inserted after preview.
    """
    code, data, cleanup, source, directory = fixture
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    if mutation == 'bytes':
        (directory / 'graphify-out/graph.json').write_text('changed')
    elif mutation == 'snapshot':
        publish_code(code, source, PROJECT, 'd' * 32)
    elif mutation == 'object':
        (directory.parent / '_objects/ff').mkdir(exist_ok=True)
        (directory.parent / '_objects/ff' / ('f' * 64 + '.json.gz')).write_bytes(b'new object')
    elif mutation == 'unknown':
        (directory / 'private-notes.txt').write_text('foreign material')
    elif mutation == 'link':
        (directory / 'link').symlink_to(source)
    else:
        value = json.loads(code.registry.read_text()); value['new-project'] = {'id': 'new-project'}
        code.registry.write_text(json.dumps(value))
    before = (directory / 'atlas-graph.json').read_bytes()
    with pytest.raises(CleanupError) as error:
        cleanup.execute(plan['planId'])
    assert error.value.code in {'CLEANUP_STALE', 'CLEANUP_UNSAFE'}
    assert (directory / 'atlas-graph.json').read_bytes() == before
    assert not (code.data_dir / AREA).exists()


@pytest.mark.parametrize('phase', ['after_journal', 'after_rename:0', 'after_catalog:projects.json', 'after_catalog:maps/data-maps/catalog.json'])
def test_precommit_failure_rolls_back_exact_catalog_and_map_bytes(fixture, phase):
    """
    Fault injection after each transaction boundary restores the preconfirm state byte-for-byte.

    Args:
        fixture: Isolated real snapshot and catalogs.
        phase: Exactly one synthetic precommit fault boundary.
    """
    code, data, cleanup, source, directory = fixture
    before = {p: p.read_bytes() for p in directory.parent.rglob('*') if p.is_file()}
    catalog_before = code.registry.read_bytes()
    def fail(current):
        """
        Raise an ordinary filesystem error at the controlled transaction boundary.

        Args:
            current: Server-authored fault phase, never a user-provided callback.
        """
        if current == phase:
            raise OSError('synthetic precommit failure')
    cleanup.fault = fail
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    final = cleanup.execute(plan['planId'])
    assert final['status'] == 'failed' and final['mapsCleared'] is False
    assert code.registry.read_bytes() == catalog_before
    assert all(p.read_bytes() == value for p, value in before.items())
    assert code.projects[PROJECT]['_graph'] == str(directory / 'atlas-graph.json')


def test_partial_purge_replay_cannot_clear_a_newer_map(fixture):
    """
    Retry touches only the original operation's quarantine and reports partial physical bytes honestly.

    Args:
        fixture: Isolated original map and service.
    """
    code, data, cleanup, source, old = fixture
    def fail(phase):
        """
        Fail only physical purge, after catalog commit has already completed.

        Args:
            phase: Trusted service phase supplied by the test hook.
        """
        if phase.startswith('before_purge:'):
            raise OSError('synthetic purge failure')
    cleanup.fault = fail
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    cleanup.execute(plan['planId'])
    partial = wait_done(cleanup, plan['planId'])
    assert partial['status'] == 'partial' and partial['mapsCleared'] and partial['canRetry']
    assert partial['removedBytes'] is None and partial['pendingPurgeBytes'] is None
    assert cleanup.storage_status()['hasPendingPhysicalData']
    newer = publish_code(code, source, PROJECT, 'e' * 32)
    cleanup.clock = lambda: time.time() + 3600
    cleanup.fault = lambda _: None
    cleanup.execute(plan['planId'])
    final = wait_done(cleanup, plan['planId'])
    assert final['status'] == 'completed' and newer.exists()
    assert code.projects[PROJECT]['_graph'] == str(newer / 'atlas-graph.json')
    assert not old.exists()


def test_foreign_registered_pointer_and_unknown_snapshot_children_block(fixture):
    """
    Catalog aliases and authored material cannot be deleted by selecting the apparent project owner.

    Args:
        fixture: Isolated selected code project and real CAS files.
    """
    code, data, cleanup, source, directory = fixture
    code.projects[OTHER] = {**code.projects[PROJECT], 'id': OTHER}
    write_json(code.registry, code.projects)
    with pytest.raises(CleanupError, match='Another registered project'):
        cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    del code.projects[OTHER]; write_json(code.registry, code.projects)
    (directory / 'empty-authored-folder').mkdir()
    with pytest.raises(CleanupError) as error:
        cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    assert error.value.code == 'CLEANUP_UNSAFE' and directory.exists()


def test_expiration_and_selection_bounds_leave_everything_unchanged(fixture):
    """
    Expired or duplicate selections never become path-based cleanup authority.

    Args:
        fixture: Isolated map and injectable service clock.
    """
    code, data, cleanup, source, directory = fixture
    with pytest.raises(CleanupError):
        cleanup.preview([{'kind': 'code', 'id': PROJECT}] * 2)
    plan = cleanup.preview([{'kind': 'code', 'id': PROJECT}])
    cleanup.clock = lambda: time.time() + 601
    with pytest.raises(CleanupError) as error:
        cleanup.execute(plan['planId'])
    assert error.value.status == 410 and directory.exists()
