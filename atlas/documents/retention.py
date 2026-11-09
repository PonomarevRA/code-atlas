"""Mark-and-sweep только для документного scope: три последних снимка и durable pins."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import parse_qs, urlsplit

from .leases import storage_lease
from .models import DocumentError, require
from .storage import read_json

_MAP_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
_HASH = re.compile(r'[a-f0-9]{64}')
_REVISION_STAGING = re.compile(r'[a-f0-9]{64}\.[a-f0-9]{32}\.staging')
_BLOB_STAGING = re.compile(r'[a-f0-9]{64}\.[a-f0-9]{32}\.tmp')
_MAX_FILES = 20000


def _files(root):
    """Проверяет весь managed subtree до удаления; ссылки и превышение бюджета запрещены."""
    root = Path(root)
    require(not root.is_symlink(), 'RETENTION_UNSAFE', 'Ссылки в managed storage запрещены.', 409)
    if not root.exists():
        return []
    result = []
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'RETENTION_UNSAFE', 'Ссылки в managed storage запрещены.', 409)
        if path.is_file():
            result.append(path)
            require(len(result) <= _MAX_FILES, 'RETENTION_LIMIT', 'Слишком много артефактов для одной очистки.', 409)
    return result


def _json(path):
    """Повреждённый reference/manifest запрещает sweep вместо предположения об отсутствии pin."""
    try:
        require(path.stat().st_size <= 16 * 1024 * 1024, 'RETENTION_LIMIT', 'Слишком большой reference manifest.', 409)
        return read_json(path)
    except (ValueError, OSError) as error:
        raise DocumentError('RETENTION_REFERENCE_INVALID', 'Не удалось проверить ссылки на снимки; удаление отменено.', 409) from error


def _pins(value, manifests, pins, *, depth=0):
    """Находит exact map/snapshot refs и ссылки documents.html, не угадывая unknown schema."""
    require(depth <= 128, 'RETENTION_REFERENCE_INVALID', 'Слишком глубокая структура ссылок.', 409)
    if isinstance(value, list):
        for child in value:
            _pins(child, manifests, pins, depth=depth + 1)
    elif isinstance(value, dict):
        map_id = value.get('documentMapId', value.get('mapId'))
        snapshot_id = value.get('documentSnapshotId', value.get('snapshotId'))
        if isinstance(map_id, str) and map_id in manifests:
            if snapshot_id is None:
                pins.update((map_id, item) for item in manifests[map_id])
            else:
                require(isinstance(snapshot_id, str) and snapshot_id in manifests[map_id], 'RETENTION_REFERENCE_MISSING', 'Ссылка ведёт на отсутствующий снимок документов.', 409)
                pins.add((map_id, snapshot_id))
        elif 'documentSnapshotId' in value and map_id is None:
            require(isinstance(snapshot_id, str), 'RETENTION_REFERENCE_INVALID', 'Неверный идентификатор document snapshot.', 409)
            matches = [(owner, snapshot_id) for owner, snapshots in manifests.items() if snapshot_id in snapshots]
            require(bool(matches), 'RETENTION_REFERENCE_INVALID', 'Неизвестная ссылка на снимок документов; удаление отменено.', 409)
            pins.update(matches)
        for key, child in value.items():
            if key in {'documentReferences', 'documentSnapshots', 'documentRefs'}:
                _validate_reference_list(child)
            _pins(child, manifests, pins, depth=depth + 1)
    elif isinstance(value, str) and 'documents.html?' in value:
        parameters = parse_qs(urlsplit(value).query)
        map_id = parameters.get('map', [None])[0]
        if isinstance(map_id, str) and map_id in manifests:
            _pins({'mapId': map_id, 'snapshotId': parameters.get('snapshot', [None])[0]}, manifests, pins, depth=depth + 1)


def _validate_reference_list(value):
    """Unknown durable reference shapes block GC until their contract is supported."""
    require(isinstance(value, list), 'RETENTION_REFERENCE_INVALID', 'Неверный список document references.', 409)
    for reference in value:
        require(isinstance(reference, dict), 'RETENTION_REFERENCE_INVALID', 'Неизвестный формат document reference.', 409)
        owner = reference.get('documentMapId', reference.get('mapId'))
        snapshot = reference.get('documentSnapshotId', reference.get('snapshotId'))
        require(isinstance(owner, str) and isinstance(snapshot, str), 'RETENTION_REFERENCE_INVALID', 'Document reference должен содержать map и snapshot.', 409)


@contextmanager
def _research_documents(data_root):
    """Блокирует research writes на время sweep и читает реальные SQLite body/document/evidence.

    Текущий research contract хранит code snapshot; будущие document refs должны
    оставаться в этих JSON полях. Неизвестная схема БД блокирует очистку.
    """
    path = data_root / 'research' / 'runs.sqlite'
    if not path.exists():
        yield []
        return
    require(not path.is_symlink(), 'RETENTION_UNSAFE', 'Research database не должна быть ссылкой.', 409)
    connection = sqlite3.connect(path, timeout=0.1)
    try:
        connection.execute('BEGIN IMMEDIATE')
        values, size = [], 0
        for row in connection.execute('SELECT body,document FROM runs'):
            for value in row:
                if value is not None:
                    size += len(value.encode('utf-8'))
                    require(size <= 16 * 1024 * 1024, 'RETENTION_LIMIT', 'Слишком много research references для одной очистки.', 409)
                    values.append(json.loads(value))
        for row in connection.execute('SELECT body FROM evidence'):
            size += len(row[0].encode('utf-8'))
            require(size <= 16 * 1024 * 1024, 'RETENTION_LIMIT', 'Слишком много research references для одной очистки.', 409)
            values.append(json.loads(row[0]))
        yield values
    except (sqlite3.Error, ValueError) as error:
        raise DocumentError('RETENTION_REFERENCE_INVALID', 'Research занят или его ссылки недоступны; удаление отменено.', 409) from error
    finally:
        connection.rollback()
        connection.close()


def _inventory(root, catalog, keep_last):
    """Собирает manifests и обязательные latest/current pins без доступа к оригиналам."""
    manifests, pins, manifest_bytes = {}, set(), 0
    for map_id, item in catalog.items():
        require(isinstance(map_id, str) and _MAP_ID.fullmatch(map_id) and isinstance(item, dict), 'RETENTION_UNSAFE', 'Неверная карта в catalog; удаление отменено.', 409)
        directory = root / 'maps' / map_id / 'snapshots'
        snapshots = {}
        for path in _files(directory):
            require(path.parent == directory and path.suffix == '.json' and _HASH.fullmatch(path.stem), 'RETENTION_UNSAFE', 'Неизвестный snapshot artifact.', 409)
            manifest_bytes += path.stat().st_size
            require(manifest_bytes <= 64 * 1024 * 1024, 'RETENTION_LIMIT', 'Слишком много manifest данных для одной очистки.', 409)
            manifest = _json(path)
            require(isinstance(manifest, dict), 'RETENTION_REFERENCE_INVALID', 'Неверный manifest снимка.', 409)
            require(manifest.get('mapId') == map_id and manifest.get('snapshotId') == path.stem and isinstance(manifest.get('documents'), list), 'RETENTION_REFERENCE_INVALID', 'Неверный manifest снимка.', 409)
            snapshots[path.stem] = (path, manifest)
        manifests[map_id] = snapshots
        latest = sorted(snapshots, key=lambda identifier: (snapshots[identifier][1].get('createdAt', ''), identifier), reverse=True)
        pins.update((map_id, identifier) for identifier in latest[:keep_last])
        if item.get('snapshotId'):
            require(item['snapshotId'] in snapshots, 'RETENTION_REFERENCE_MISSING', 'Текущий снимок отсутствует; удаление отменено.', 409)
            pins.add((map_id, item['snapshotId']))
    return manifests, pins


def _candidates(root, manifests, pins):
    """Удаляемые snapshots, revisions и blobs вычисляются из одного замороженного mark set."""
    snapshots, revisions, blobs = [], set(), set()
    for map_id, items in manifests.items():
        for identifier, (path, manifest) in items.items():
            if (map_id, identifier) not in pins:
                snapshots.append(path)
                continue
            for binding in manifest['documents']:
                require(isinstance(binding, dict), 'RETENTION_REFERENCE_INVALID', 'Неверный document binding.', 409)
                revision, blob = binding.get('revisionId'), binding.get('blobHash')
                require(isinstance(revision, str) and _HASH.fullmatch(revision) and isinstance(blob, str) and _HASH.fullmatch(blob), 'RETENTION_REFERENCE_INVALID', 'Manifest содержит неверную ссылку на артефакт.', 409)
                revisions.add(revision)
                blobs.add(blob)
    revision_paths, blob_paths = [], []
    for path in (root / 'revisions').glob('*'):
        known_name = _HASH.fullmatch(path.name) or _REVISION_STAGING.fullmatch(path.name)
        require(path.is_dir() and not path.is_symlink() and known_name, 'RETENTION_UNSAFE', 'Неизвестный revision artifact.', 409)
        if path.name not in revisions:
            revision_paths.append(path)
    for path in (root / 'blobs').glob('*'):
        known_name = _HASH.fullmatch(path.name) or _BLOB_STAGING.fullmatch(path.name)
        require(path.is_file() and not path.is_symlink() and known_name, 'RETENTION_UNSAFE', 'Неизвестный blob artifact.', 409)
        if path.name not in blobs:
            blob_paths.append(path)
    return snapshots, revision_paths, blob_paths


def collect(root, *, keep_last=3, dry_run=True):
    """Удаляет только unpinned artifacts под exclusive межпроцессным барьером.

    Порядок snapshot → revision → blob допускает crash лишь с утечкой места,
    но никогда с manifest, который ссылается на уже удалённый индекс.
    """
    require(type(keep_last) is int and 3 <= keep_last <= 100, 'INVALID_RETENTION', 'Хранится минимум три последних снимка каждой карты.')
    require(type(dry_run) is bool, 'INVALID_RETENTION', 'dryRun должен быть boolean.')
    root = Path(root)
    data_root = root.parent.parent
    with storage_lease(root, exclusive=True, wait=False):
        catalog = _json(root / 'catalog.json') if (root / 'catalog.json').exists() else {}
        jobs = _json(root / 'jobs.json') if (root / 'jobs.json').exists() else {}
        require(isinstance(catalog, dict) and isinstance(jobs, dict) and all(isinstance(job, dict) for job in jobs.values()), 'RETENTION_REFERENCE_INVALID', 'Неверный catalog/jobs; удаление отменено.', 409)
        require(not any(job.get('status') in {'queued', 'running'} for job in jobs.values()), 'STORE_BUSY', 'Очистка недоступна во время импорта.', 409)
        _files(root / 'maps')
        _files(root / 'revisions')
        _files(root / 'blobs')
        require(all(path.name in catalog for path in (root / 'maps').glob('*')), 'RETENTION_UNSAFE', 'Неизвестная карта в storage; удаление отменено.', 409)
        manifests, pins = _inventory(root, catalog, keep_last)
        references = [*_files(data_root / 'document-maps' / 'views')]
        search_refs = data_root / 'document-search' / 'references.json'
        if search_refs.exists():
            require(not search_refs.is_symlink(), 'RETENTION_UNSAFE', 'Reference registry не должен быть ссылкой.', 409)
            references.append(search_refs)
        reference_bytes = 0
        for path in references:
            reference_bytes += path.stat().st_size
            require(reference_bytes <= 16 * 1024 * 1024, 'RETENTION_LIMIT', 'Слишком много сохранённых ссылок для одной очистки.', 409)
            require(path.suffix == '.json', 'RETENTION_REFERENCE_INVALID', 'Неизвестный формат reference registry.', 409)
            reference = _json(path)
            if path == search_refs:
                require(isinstance(reference, dict) and reference.get('version') == 1, 'RETENTION_REFERENCE_INVALID', 'Неизвестная версия search references.', 409)
                _validate_reference_list(reference.get('references'))
            else:
                _validate_reference_list([reference])
            _pins(reference, manifests, pins)
        with _research_documents(data_root) as research:
            _pins(research, manifests, pins)
            snapshots, revisions, blobs = _candidates(root, manifests, pins)
            candidates = [*snapshots, *revisions, *blobs]
            reclaimable = 0
            for path in candidates:
                files = _files(path) if path.is_dir() else [path]
                for file in files:
                    reclaimable += file.stat().st_size
            if not dry_run:
                for path in candidates:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            return {'dryRun': dry_run, 'keepLast': keep_last, 'scopeMapIds': sorted(manifests)[:100],
                    'scopeMapCount': len(manifests), 'pinnedSnapshots': len(pins),
                    'removedSnapshots': [path.stem for path in snapshots[:100]],
                    'unreferencedRevisions': [path.name for path in revisions[:100]],
                    'unreferencedBlobs': [path.name for path in blobs[:100]],
                    'snapshotCount': len(snapshots), 'revisionCount': len(revisions), 'blobCount': len(blobs),
                    'listsTruncated': max(len(manifests), len(snapshots), len(revisions), len(blobs)) > 100,
                    'reclaimableBytes': reclaimable, 'reclaimedBytes': 0 if dry_run else reclaimable, 'deletionAvailable': True}
