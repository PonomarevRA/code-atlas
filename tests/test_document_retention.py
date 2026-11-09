"""Safe retention: real indexes, durable references and cross-process leases."""
from contextlib import contextmanager
import multiprocessing
from pathlib import Path
import sqlite3
import time

import pytest

from atlas.documents import DocumentError, DocumentStore, TreeBuilder
from atlas.documents.leases import storage_lease
from atlas.documents.models import identity
from atlas.documents.storage import atomic_json, write_revision


@contextmanager
def seeded_store(tmp_path, count=6):
    """Build actual immutable revision/index/blob fixtures without running format parsers."""
    project = tmp_path / 'source'
    project.mkdir()
    store = DocumentStore(tmp_path / 'data', project, 'fixture')
    snapshots = []
    for number in range(count):
        text = 'document version ' + str(number)
        revision, blob, snapshot = identity('revision', number), identity('blob', number), identity('snapshot', number)
        tree = TreeBuilder('source.md')
        tree.add('section', tree.root_id, label='Section', text=text)
        write_revision(store.root / 'revisions' / revision, revision, tree.build(),
                       {'providerVersion': 'fixture-v1', 'sourceHash': blob})
        captured = store.root / 'blobs' / blob
        captured.parent.mkdir(exist_ok=True)
        captured.write_text(text)
        binding = {'revisionId': revision, 'blobHash': blob, 'documentId': identity('source.md'), 'path': 'source.md', 'sourceHash': blob, 'coverage': {'status': 'complete'}}
        manifest = {'mapId': 'map', 'snapshotId': snapshot, 'createdAt': f'2026-10-21T00:00:{number:02d}Z', 'documents': [binding]}
        atomic_json(store.root / 'maps' / 'map' / 'snapshots' / (snapshot + '.json'), manifest)
        snapshots.append(snapshot)
    store.catalog = {'map': {'id': 'map', 'snapshotId': snapshots[-1], 'status': 'ready'}}
    atomic_json(store.catalog_path, store.catalog)
    try:
        yield store, snapshots
    finally:
        store.close()


def hold_lease(root, ready, release):
    """Independent spawned process keeps an active reader alive for the race test."""
    with storage_lease(Path(root)):
        ready.set()
        release.wait(15)


def test_collect_keeps_three_and_deletes_only_unreachable_bytes(tmp_path):
    with seeded_store(tmp_path) as (store, snapshots):
        original = tmp_path / 'source' / 'keep.txt'
        original.write_text('outside managed data')
        dry = store.retention()
        assert dry['dryRun'] and dry['reclaimedBytes'] == 0
        assert set(dry['removedSnapshots']) == set(snapshots[:3])
        assert len(list((store.root / 'revisions').iterdir())) == 6
        result = store.retention(dry_run=False)
        assert result['reclaimedBytes'] == dry['reclaimableBytes'] > 0
        assert len(list((store.root / 'revisions').iterdir())) == 3
        assert len(list((store.root / 'blobs').iterdir())) == 3
        for snapshot in snapshots[-3:]:
            assert store.graph('map', snapshot)['nodes']
        with pytest.raises(DocumentError, match='Снимок'):
            store.snapshot('map', snapshots[0])
        assert original.read_text() == 'outside managed data'
        assert store.retention(dry_run=False)['reclaimedBytes'] == 0
        with pytest.raises(DocumentError) as error:
            store.retention(keep_last=2, dry_run=False)
        assert error.value.code == 'INVALID_RETENTION'


def test_views_search_and_research_pin_exact_older_snapshots(tmp_path):
    with seeded_store(tmp_path, 7) as (store, snapshots):
        data = tmp_path / 'data'
        atomic_json(data / 'document-maps' / 'views' / 'view.json', {'mapId': 'map', 'snapshotId': snapshots[0]})
        atomic_json(data / 'document-search' / 'references.json', {'version': 1, 'references': [{'mapId': 'map', 'snapshotId': snapshots[1]}]})
        research = data / 'research' / 'runs.sqlite'
        research.parent.mkdir()
        with sqlite3.connect(research) as database:
            database.executescript('CREATE TABLE runs(body TEXT, document TEXT); CREATE TABLE evidence(body TEXT);')
            database.execute('INSERT INTO runs VALUES(?,NULL)', ('{"projectId":"code","snapshotId":"code-snapshot"}',))
            from json import dumps
            database.execute('INSERT INTO evidence VALUES(?)', (dumps({'documentMapId': 'map', 'documentSnapshotId': snapshots[2]}),))
        result = store.retention(dry_run=False)
        assert result['removedSnapshots'] == [snapshots[3]]
        for snapshot in [*snapshots[:3], *snapshots[-3:]]:
            assert store.graph('map', snapshot)['nodes']


def test_missing_or_malformed_reference_fails_closed(tmp_path):
    with seeded_store(tmp_path) as (store, snapshots):
        view = tmp_path / 'data' / 'document-maps' / 'views' / 'broken.json'
        atomic_json(view, {'mapId': 'map', 'snapshotId': identity('missing')})
        with pytest.raises(DocumentError) as error:
            store.retention(dry_run=False)
        assert error.value.code == 'RETENTION_REFERENCE_MISSING'
        view.write_text('broken JSON')
        with pytest.raises(DocumentError) as error:
            store.retention(dry_run=False)
        assert error.value.code == 'RETENTION_REFERENCE_INVALID'
        assert len(list((store.root / 'revisions').iterdir())) == 6
        assert store.snapshot('map', snapshots[0])


def test_snapshot_lease_blocks_gc_until_reader_finishes(tmp_path):
    with seeded_store(tmp_path) as (store, snapshots):
        with store.snapshot_lease('map', snapshots[0]):
            with pytest.raises(DocumentError) as error:
                store.retention(dry_run=False)
            assert error.value.code == 'STORE_BUSY'
            assert store.graph('map', snapshots[0])['nodes']
        assert snapshots[0] in store.retention(dry_run=False)['removedSnapshots']


def test_cross_process_reader_and_crash_release_protect_artifacts(tmp_path):
    with seeded_store(tmp_path) as (store, snapshots):
        context = multiprocessing.get_context('spawn')
        ready, release = context.Event(), context.Event()
        process = context.Process(target=hold_lease, args=(str(store.root), ready, release))
        process.start()
        try:
            assert ready.wait(8)
            with pytest.raises(DocumentError) as error:
                store.retention(dry_run=False)
            assert error.value.code == 'STORE_BUSY'
            assert store.snapshot('map', snapshots[0])
            process.terminate()
            process.join(5)
            assert not process.is_alive()
            assert snapshots[0] in store.retention(dry_run=False)['removedSnapshots']
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)


def test_pending_jobs_and_symlinked_artifacts_prevent_cleanup(tmp_path):
    with seeded_store(tmp_path) as (store, snapshots):
        atomic_json(store.jobs_path, {'job': {'status': 'queued'}})
        with pytest.raises(DocumentError) as error:
            store.retention(dry_run=False)
        assert error.value.code == 'STORE_BUSY'
        atomic_json(store.jobs_path, {})
        target = store.root / 'blobs' / identity('evil')
        target.symlink_to(tmp_path / 'source')
        with pytest.raises(DocumentError) as error:
            store.retention(dry_run=False)
        assert error.value.code == 'RETENTION_UNSAFE'
        assert store.snapshot('map', snapshots[0])


class RetentionFixtureProvider:
    """A small real subprocess provider supports automatic publication/failure tests."""
    id, version, runtime_fingerprint = 'retention-fixture', '1', '1'

    def parse(self, path, options, context):
        """Produce one immutable content node, or intentionally fail before publication."""
        if options.get('fail'):
            raise ValueError('Expected fixture failure')
        time.sleep(options.get('delay', 0))
        builder = TreeBuilder('source.md')
        builder.add('section', builder.root_id, text=path.read_text())
        return builder.build()


def retention_fixture_resolver(path, **options):
    """Picklable resolver keeps subprocess imports separate from test-local closures."""
    return RetentionFixtureProvider()


def test_auto_collection_runs_only_after_successful_publication(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    source = project / 'source.md'
    store = DocumentStore(tmp_path / 'data', project, provider_resolver=retention_fixture_resolver, auto_retention=True)
    try:
        snapshots = []
        for number in range(5):
            source.write_text(str(number))
            job = store.ensure('map', ['source.md'])
            result = store.wait(job['id'], 10)
            assert result['status'] == 'completed', result
            snapshots.append(result['snapshotId'])
        assert len(list((store.root / 'maps' / 'map' / 'snapshots').glob('*.json'))) == 3
        job = store.ensure('map', ['source.md'], options={'fail': True})
        result = store.wait(job['id'], 10)
        assert result['status'] == 'failed'
        assert store.snapshot('map')['snapshotId'] == snapshots[-1]
        assert all(store.graph('map', snapshot)['nodes'] for snapshot in snapshots[-3:])
    finally:
        store.close()


def test_scoped_retention_http_defaults_to_preview_and_checks_project(tmp_path):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from atlas.document_service import DocumentService
    from atlas.document_retention_api import register_document_retention_routes

    with seeded_store(tmp_path) as (store, snapshots):
        service = DocumentService(SimpleNamespace(data_dir=tmp_path / 'data'))
        binding = {'id': 'map', 'root': str(store.project_root), 'projectId': 'fixture', 'title': 'Documents'}
        service.bindings['map'] = binding
        service.stores[identity(binding['root'], binding['projectId'])] = store
        app = FastAPI()

        @app.exception_handler(DocumentError)
        async def document_error(request, error):
            """Use the production error shape for a small real HTTP scope fixture."""
            return JSONResponse({'code': error.code}, status_code=error.status)

        register_document_retention_routes(app, service)
        with TestClient(app) as client:
            response = client.post('/api/document-maps/map/retention', json={})
            assert response.status_code == 200
            assert response.json()['dryRun'] is True
            assert len(list((store.root / 'revisions').iterdir())) == 6
            denied = client.post('/api/document-maps/map/retention', json={'projectId': 'other', 'dryRun': False})
            assert denied.status_code == 403
            assert len(list((store.root / 'revisions').iterdir())) == 6
            assert client.post('/api/document-maps/map/retention', json={'keepLast': 1}).status_code == 422
            cleaned = client.post('/api/document-maps/map/retention', json={'projectId': 'fixture', 'dryRun': False})
            assert cleaned.status_code == 200
            assert cleaned.json()['snapshotCount'] == 3


def test_shared_revision_survives_another_maps_snapshot_cleanup(tmp_path):
    with seeded_store(tmp_path) as (store, snapshots):
        old = store.snapshot('map', snapshots[0])
        other_snapshot = identity('other-map-snapshot')
        atomic_json(store.root / 'maps' / 'other' / 'snapshots' / (other_snapshot + '.json'),
                    {**old, 'mapId': 'other', 'snapshotId': other_snapshot})
        store.catalog['other'] = {'id': 'other', 'snapshotId': other_snapshot, 'status': 'ready'}
        atomic_json(store.catalog_path, store.catalog)
        result = store.retention(dry_run=False)
        assert snapshots[0] in result['removedSnapshots']
        assert len(list((store.root / 'revisions').iterdir())) == 4
        assert store.graph('other', other_snapshot)['nodes']


def test_cancelled_job_keeps_background_index_writer_leased(tmp_path, monkeypatch):
    from threading import Event
    import atlas.documents.store as store_module

    project = tmp_path / 'project'
    project.mkdir()
    (project / 'source.md').write_text('source')
    store = DocumentStore(tmp_path / 'data', project, provider_resolver=retention_fixture_resolver)
    entered, release = Event(), Event()
    original = store_module.write_revision

    def paused_revision(*args, **kwargs):
        """Pause after extraction to reproduce cancellation while SQLite publication is pending."""
        entered.set()
        assert release.wait(8)
        return original(*args, **kwargs)

    monkeypatch.setattr(store_module, 'write_revision', paused_revision)
    try:
        job = store.ensure('map', ['source.md'])
        assert entered.wait(8)
        store.cancel(job['id'])
        assert store.wait(job['id'], 5)['status'] == 'cancelled'
        with pytest.raises(DocumentError) as error:
            store.retention(dry_run=False)
        assert error.value.code == 'STORE_BUSY'
        release.set()
        store.parsers.submit(lambda: None).result(8)
        assert store.retention(dry_run=False)['revisionCount'] == 1
    finally:
        release.set()
        store.close()


def test_abandoned_known_staging_is_reclaimed_without_touching_unknown_files(tmp_path):
    with seeded_store(tmp_path) as (store, snapshots):
        staging = store.root / 'revisions' / (identity('staging') + '.' + 'a' * 32 + '.staging')
        staging.mkdir()
        (staging / 'index.sqlite').write_bytes(b'partial')
        blob = store.root / 'blobs' / (identity('staging-blob') + '.' + 'b' * 32 + '.tmp')
        blob.write_bytes(b'partial blob')
        result = store.retention(dry_run=False)
        assert result['revisionCount'] == 4
        assert not staging.exists() and not blob.exists()
        foreign = store.root / 'revisions' / 'manual-user-folder'
        foreign.mkdir()
        (foreign / 'notes.txt').write_text('keep')
        with pytest.raises(DocumentError) as error:
            store.retention(dry_run=False)
        assert error.value.code == 'RETENTION_UNSAFE'
        assert (foreign / 'notes.txt').read_text() == 'keep'
