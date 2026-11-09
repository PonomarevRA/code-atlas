"""Storage cutover, missing evidence and explicit repair preserve map identity and history."""
import json
from pathlib import Path
import shutil
import sqlite3
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from atlas.data_models import DataError, DataInput, DataQuery
from atlas.data_store import DataMapStore
from atlas.server import create_app
from test_data_maps import build, query, wait


@pytest.fixture
def published(tmp_path):
    """Publish small real CSV evidence rather than mocking availability checks."""
    source = tmp_path / 'input'
    source.mkdir()
    (source / 'rows.csv').write_text('id,name\n001,Alice\n')
    store = DataMapStore(tmp_path / 'state')
    overview = build(store, source, ['rows.csv'])
    yield store, source, overview
    store.close()


def snapshot_path(store, overview):
    """Resolve the canonical fixture snapshot without any implicit import."""
    return store.root / overview['id'] / overview['snapshotId']


def legacy_catalog(store):
    """Move only private test artifacts into the supported pre-maps layout."""
    store.close()
    legacy = store.data_dir / 'data-maps'
    store.root.rename(legacy)
    store.root.parent.rmdir()
    return legacy


def test_legacy_reads_are_side_effect_free_and_first_mutation_cuts_over(published):
    """A complete catalog moves on mutation; startup/GET preserve every old file byte."""
    original, source, overview = published
    legacy = legacy_catalog(original)
    before = {path.relative_to(original.data_dir): path.read_bytes() for path in legacy.rglob('*') if path.is_file()}
    store = DataMapStore(original.data_dir)
    try:
        assert store.list()['maps'][0]['id'] == overview['id']
        assert store.overview(overview['id'])['records'] == 1
        assert query(store, overview, 'schema')['items']
        assert store.technical()['maps'][0]['sizeBytes'] > 0
        assert store.executor is None and not store.root.exists()
        assert {path.relative_to(store.data_dir): path.read_bytes() for path in legacy.rglob('*') if path.is_file()} == before
        (source / 'next.csv').write_text('id\n002\n')
        added = build(store, source, ['next.csv'])
        catalog = json.loads(store.registry.read_text())
        assert set(catalog) == {overview['id'], added['id']}
        assert catalog[overview['id']]['snapshotId'] == overview['snapshotId']
        assert snapshot_path(store, added).is_dir()
        assert not snapshot_path(store, overview).exists()
        assert query(store, overview, 'schema')['items']
        assert {path.relative_to(store.data_dir): path.read_bytes() for path in legacy.rglob('*') if path.is_file()} == before
        # Canonical authority is intentional even when an old catalog has additional IDs.
        store.registry.write_text(json.dumps({added['id']: catalog[added['id']]}))
        restarted = DataMapStore(store.data_dir)
        try:
            assert [row['id'] for row in restarted.list()['maps']] == [added['id']]
        finally:
            restarted.close()
    finally:
        store.close()


def test_canonical_catalog_does_not_fallback_when_corrupt(published):
    """The old catalog cannot silently resurrect after a canonical catalog is damaged."""
    original, _, _ = published
    legacy = original.data_dir / 'data-maps'
    legacy.mkdir()
    shutil.copyfile(original.registry, legacy / 'catalog.json')
    original.registry.write_text('{broken')
    with pytest.raises((DataError, ValueError)):
        DataMapStore(original.data_dir)


def damage(snapshot, kind):
    """Remove or corrupt one required artifact while leaving source files available."""
    if kind == 'snapshot':
        shutil.rmtree(snapshot)
    elif kind == 'metadata-missing':
        (snapshot / 'metadata.json').unlink()
    elif kind == 'metadata-json':
        (snapshot / 'metadata.json').write_text('{broken')
    elif kind == 'metadata-shape':
        (snapshot / 'metadata.json').write_text(json.dumps({'mapId': 'wrong'}))
    elif kind == 'metadata-fields':
        metadata = json.loads((snapshot / 'metadata.json').read_text())
        next(iter(metadata['collections'].values()))['fields'] = [{'name': '/id'}]
        (snapshot / 'metadata.json').write_text(json.dumps(metadata))
    elif kind == 'database-missing':
        (snapshot / 'records.sqlite').unlink()
    elif kind == 'database-bytes':
        (snapshot / 'records.sqlite').write_bytes(b'not sqlite')
    elif kind == 'database-schema':
        (snapshot / 'records.sqlite').unlink()
        with sqlite3.connect(snapshot / 'records.sqlite') as connection:
            connection.execute('CREATE TABLE records(id TEXT)')


@pytest.mark.parametrize('kind,code', [
    ('snapshot', 'SNAPSHOT_MISSING'), ('metadata-missing', 'SNAPSHOT_MISSING'),
    ('database-missing', 'SNAPSHOT_MISSING'), ('metadata-json', 'SNAPSHOT_CORRUPT'),
    ('metadata-shape', 'SNAPSHOT_CORRUPT'), ('metadata-fields', 'SNAPSHOT_CORRUPT'),
    ('database-bytes', 'SNAPSHOT_CORRUPT'), ('database-schema', 'SNAPSHOT_CORRUPT'),
])
def test_unavailable_evidence_isolated_from_catalog_and_explicitly_repaired(published, kind, code):
    """GET stays useful, direct reads fail honestly and only refresh publishes replacement evidence."""
    original, source, overview = published
    original.close()
    damage(snapshot_path(original, overview), kind)
    catalog_before = original.registry.read_bytes()
    app = create_app(original.data_dir)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        store = app.state.data_maps
        paths = ['/api/data-maps', '/api/technical/storage', '/api/data-maps/' + overview['id']]
        for endpoint in paths:
            response = client.get(endpoint)
            assert response.status_code == 200
            value = response.json()
            row = value['maps'][0] if 'maps' in value else value
            assert row['freshness']['status'] == 'unavailable'
            assert row['freshness']['code'] == code
            assert row['status'] == 'unavailable'
            assert row['snapshotId'] == overview['snapshotId']
            assert 'records' not in row and 'inputBytes' not in row
        assert store.maps[overview['id']]['status'] == 'ready'
        with pytest.raises(DataError) as direct:
            store.snapshot(overview['id'], overview['snapshotId'])
        assert direct.value.code == code and direct.value.status == 409
        response = client.post('/api/data-maps/' + overview['id'] + '/query/schema', json={'snapshotId': overview['snapshotId']})
        assert response.status_code == 409 and response.json()['code'] == code
        exported = client.get('/api/data-maps/' + overview['id'] + '/export', params={'snapshotId': overview['snapshotId']})
        assert exported.status_code == 409 and exported.json()['code'] == code
        assert store.executor is None and not store.jobs
        assert original.registry.read_bytes() == catalog_before
        repaired = client.post('/api/data-maps/' + overview['id'] + '/refresh', json={})
        assert repaired.status_code == 202
        assert wait(store, repaired.json())['status'] == 'completed'
        current = store.overview(overview['id'])
        assert current['snapshotId'] != overview['snapshotId'] and current['records'] == 1
        assert query(store, current, 'schema')['items']


def test_existing_bad_canonical_snapshot_never_falls_back_to_legacy(published):
    """A matching legacy copy is considered only when the canonical directory is absent."""
    store, _, overview = published
    canonical = snapshot_path(store, overview)
    legacy = store.data_dir / 'data-maps' / overview['id'] / overview['snapshotId']
    legacy.parent.mkdir(parents=True)
    shutil.copytree(canonical, legacy)
    damage(canonical, 'metadata-json')
    assert store.overview(overview['id'])['freshness']['code'] == 'SNAPSHOT_CORRUPT'
    shutil.rmtree(canonical)
    assert query(store, overview, 'schema')['items']


def test_data_saved_views_read_both_layouts_with_canonical_priority(published):
    """Historical views survive cutover and new view creation targets maps only."""
    store, _, overview = published
    metadata = store.snapshot(overview['id'])[1]
    collection = next(iter(metadata['collections']))
    record = query(store, overview, 'records', collectionId=collection)['items'][0]['id']
    view = query(store, overview, 'view', recordId=record)
    canonical = store.root / overview['id'] / 'views' / (view['id'] + '.json')
    assert canonical.is_file()
    legacy = store.data_dir / 'data-maps' / overview['id'] / 'views'
    legacy.mkdir(parents=True)
    old = {**view, 'title': 'legacy'}
    (legacy / canonical.name).write_text(json.dumps(old))
    assert store.saved_view(overview['id'], view['id'])['title'] == view['title']
    canonical.unlink()
    assert store.saved_view(overview['id'], view['id'])['title'] == 'legacy'


@pytest.mark.parametrize('kind', ['map-id', 'snapshot-id', 'snapshot-link', 'metadata-link', 'database-link', 'map-file'])
def test_storage_violations_are_explicit_and_do_not_read_external_files(published, tmp_path, kind):
    """Malformed persisted identifiers and replaced path components cannot escape managed roots."""
    store, _, overview = published
    snapshot = snapshot_path(store, overview)
    external = tmp_path / 'external'
    external.mkdir()
    secret = external / 'secret'
    secret.write_text('untouched')
    if kind == 'map-id':
        store.maps[overview['id']]['id'] = '../external'
    elif kind == 'snapshot-id':
        store.maps[overview['id']]['snapshotId'] = '../external'
    elif kind == 'snapshot-link':
        shutil.rmtree(snapshot)
        snapshot.symlink_to(external, target_is_directory=True)
    elif kind in {'metadata-link', 'database-link'}:
        target = snapshot / ('metadata.json' if kind == 'metadata-link' else 'records.sqlite')
        target.unlink()
        target.symlink_to(secret)
    else:
        directory = snapshot.parent
        shutil.rmtree(directory)
        directory.write_text('unknown file')
    assert store.list()['maps'][0]['freshness']['code'] == 'INVALID_STORAGE'
    assert store.technical()['maps'][0]['freshness']['code'] == 'INVALID_STORAGE'
    with pytest.raises(DataError) as error:
        store.snapshot(overview['id'])
    assert error.value.code == 'INVALID_STORAGE'
    assert secret.read_text() == 'untouched'


def test_unknown_staging_is_preserved_on_collision(published, monkeypatch):
    """Failure to create private staging never authorizes removing a pre-existing object."""
    store, _, overview = published
    job_id = 'f' * 32
    unknown = store.directory(overview['id']) / ('.staging-' + job_id)
    unknown.mkdir()
    (unknown / 'keep').write_text('unknown owner')
    monkeypatch.setattr('atlas.data_store.uuid4', lambda: SimpleNamespace(hex=job_id))
    job = wait(store, store.refresh(overview['id'], force=True))
    assert job['status'] == 'failed'
    assert (unknown / 'keep').read_text() == 'unknown owner'
    assert store.overview(overview['id'])['snapshotId'] == overview['snapshotId']


def test_stop_signal_does_not_wait_for_catalog_lock(published):
    """HTTP lock contention cannot delay desktop cancellation or admit later work."""
    store, source, overview = published
    acquired, release, stopped = Event(), Event(), Event()

    def hold_lock():
        """Model another handler holding the catalog mutex while reading disk."""
        with store.lock:
            acquired.set()
            release.wait(3)

    holder = Thread(target=hold_lock)
    holder.start()
    assert acquired.wait(1)
    stopper = Thread(target=lambda: (store.request_stop(), stopped.set()))
    stopper.start()
    try:
        assert stopped.wait(.5)
    finally:
        release.set()
        holder.join(2)
        stopper.join(2)
    assert all(event.is_set() for event in store.cancel_events.values())
    for action in (lambda: store.refresh(overview['id']), lambda: store.create(DataInput(files=['rows.csv']), source)):
        with pytest.raises(DataError) as error:
            action()
        assert error.value.code == 'SERVICE_STOPPING' and error.value.status == 503


def test_legacy_folder_open_matches_technical_path(published, monkeypatch):
    """The folder action opens existing legacy storage without materializing maps on GET."""
    original, _, overview = published
    legacy = legacy_catalog(original)
    app = create_app(original.data_dir)
    calls = []
    monkeypatch.setattr('atlas.data_api.subprocess.Popen', lambda args, **kwargs: calls.append(args))
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        response = client.get('/api/technical/storage')
        path = str(legacy / overview['id'])
        assert response.json()['maps'][0]['storagePath'] == path
        assert client.post('/api/technical/open-storage', json={'mapId': overview['id']}).status_code == 200
        assert calls[0][-1] == path
        assert not app.state.data_maps.root.exists()


def test_legacy_data_view_uuid_collision_cannot_shadow_its_immutable_url(published, monkeypatch):
    """A legacy data-view URL cannot acquire different evidence after the maps cutover."""
    store, _, overview = published
    metadata = store.snapshot(overview['id'])[1]
    collection = next(iter(metadata['collections']))
    record_id = query(store, overview, 'records', collectionId=collection)['items'][0]['id']
    original = query(store, overview, 'view', recordId=record_id)
    canonical = store.root / overview['id'] / 'views' / (original['id'] + '.json')
    legacy = store.data_dir / 'data-maps' / overview['id'] / 'views' / canonical.name
    legacy.parent.mkdir(parents=True)
    canonical.rename(legacy)
    before = legacy.read_bytes()
    monkeypatch.setattr('atlas.data_store.uuid4', lambda: SimpleNamespace(hex=original['id']))
    with pytest.raises(DataError) as error:
        query(store, overview, 'view', recordId=record_id, title='shadow attempt')
    assert error.value.code == 'INVALID_STORAGE' and error.value.status == 409
    assert store.saved_view(overview['id'], original['id']) == original
    assert legacy.read_bytes() == before
    assert not canonical.exists()
