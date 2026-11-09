"""
Durable cleanup staging, two-catalog rollback and restart recovery for confirmed operations only.
"""
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .map_storage import checked_path
from .map_cleanup_inventory import CleanupError, roots, stamp, target_allowed

SCHEMA = 'atlas-map-cleanup-v1'
AREA = '_map-cleanup'
MAX_JOURNAL_BYTES = 16 * 1024 * 1024
CATALOGS = ('projects.json', 'maps/data-maps/catalog.json')
READ_CATALOGS = (*CATALOGS, 'data-maps/catalog.json')


def _sync_directory(path):
    """
    Persist directory rename entries on platforms providing directory fsync.

    Args:
        path: Already checked managed directory; no client-controlled path is used.
    Returns:
        None; unsupported directory fsync does not weaken atomic file replacement semantics.
    """
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_bytes(path, content):
    """
    Durably replace one checked managed file with an exclusively created private temporary file.

    Args:
        path: Server-derived checked target, including fixed catalogs or a transaction journal.
        content: Bounded complete UTF-8/binary bytes already prepared by the caller.
    Returns:
        None after replacement and parent-directory fsync.
    Raises:
        OSError: Publication failed; callers decide precommit rollback versus postcommit partial status.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def encoded(value):
    """
    Encode deterministic catalog/journal JSON without evaluating input values.

    Args:
        value: Server-owned JSON-compatible descriptor.
    Returns:
        Complete UTF-8 bytes suitable for durable atomic replacement.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def journal_area(root, *, create=False):
    """
    Validate the fixed owned journal area; unknown preexisting directories are never adopted.

    Args:
        root: Trusted application data directory.
        create: Permit first explicit confirmed execution to create the managed marker.
    Returns:
        Checked journal area, or None when absent and creation was not requested.
    Raises:
        CleanupError: A redirected, conflicting or unrecognized area must be preserved.
    """
    try:
        area = checked_path(root, AREA)
        if not area.exists():
            if not create:
                return None
            area.mkdir()
            atomic_bytes(area / 'owner.json', encoded({'schemaVersion': SCHEMA}))
        marker = checked_path(root, AREA, 'owner.json', directory=False)
        if not marker.is_file() or marker.stat().st_size > 1024 or json.loads(marker.read_text()) != {'schemaVersion': SCHEMA}:
            raise ValueError('unrecognized marker')
        return area
    except (ValueError, OSError) as error:
        raise CleanupError('CLEANUP_UNSAFE', 'Cleanup journal storage is unavailable or unrecognized.') from error


def operation_directory(root, plan_id, *, create=False):
    """
    Resolve exactly one opaque operation directory below the marked journal area.

    Args:
        root: Trusted application data directory.
        plan_id: Server-generated32-character lowercase hexadecimal operation identity.
        create: Create the new directory exclusively after confirmation.
    Returns:
        Checked private operation directory.
    Raises:
        CleanupError: Invalid identity, path conflict or unknown journal ownership.
    """
    if not isinstance(plan_id, str) or not re.fullmatch(r'[a-f0-9]{32}', plan_id):
        raise CleanupError('CLEANUP_PLAN_MISSING', 'Cleanup plan was not found.', 404)
    area = journal_area(root, create=create)
    if area is None:
        raise CleanupError('CLEANUP_PLAN_MISSING', 'Cleanup plan was not found.', 404)
    try:
        directory = checked_path(root, AREA, plan_id)
        if create:
            directory.mkdir()
        return directory
    except (ValueError, OSError) as error:
        raise CleanupError('CLEANUP_CONFLICT', 'Cleanup operation storage conflicts with an existing object.') from error


def save(root, journal):
    """
    Publish a bounded complete journal state; the initial state precedes every destructive rename.

    Args:
        root: Trusted application data directory.
        journal: Validated server-owned operation descriptor with opaque planId.
    Returns:
        None after atomic durable publication.
    Raises:
        CleanupError: Journal size exceeds the bounded transaction budget.
        OSError: Durable publication failed.
    """
    content = encoded(journal)
    if len(content) > MAX_JOURNAL_BYTES:
        raise CleanupError('CLEANUP_LIMIT', 'Cleanup transaction metadata exceeds the safe journal budget.')
    directory = operation_directory(root, journal['planId'])
    path = checked_path(root, *directory.relative_to(root).parts, 'journal.json', directory=False)
    atomic_bytes(path, content)


def catalog_image(root, relative):
    """
    Capture exact existing catalog bytes or explicit absence for rollback and stale detection.

    Args:
        root: Trusted application storage root.
        relative: One of the two fixed catalog names, never an arbitrary journal path.
    Returns:
        Existence, SHA256 and base64 content descriptor; at most4MiB per catalog.
    Raises:
        CleanupError: Catalog path/type/link or byte budget is unsafe.
    """
    if relative not in READ_CATALOGS:
        raise CleanupError('CLEANUP_UNSAFE', 'Unknown cleanup catalog.')
    try:
        path = checked_path(root, *Path(relative).parts, directory=False)
        if not path.exists():
            return {'exists': False, 'bytes': '', 'hash': None}
        stamp(path)
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('catalog too large')
        content = path.read_bytes()
        json.loads(content)
        return {'exists': True, 'bytes': base64.b64encode(content).decode(), 'hash': sha256(content).hexdigest()}
    except (ValueError, OSError) as error:
        raise CleanupError('CLEANUP_UNSAFE', 'Map catalog is unsafe, unavailable or too large.') from error


def publish_catalog(root, relative, image):
    """
    Restore or publish a fixed checked catalog image; no arbitrary journal destination is accepted.

    Args:
        root: Trusted application storage root.
        relative: One of the two fixed catalog filenames.
        image: Exact validated existence/base64/hash image.
    Returns:
        None after durable replacement/removal.
    Raises:
        CleanupError: Image or fixed catalog path is invalid.
        OSError: Filesystem publication failed.
    """
    if relative not in CATALOGS:
        raise CleanupError('CLEANUP_UNSAFE', 'Unknown cleanup catalog.')
    path = checked_path(root, *Path(relative).parts, directory=False)
    if image['exists']:
        content = base64.b64decode(image['bytes'], validate=True)
        if sha256(content).hexdigest() != image['hash']:
            raise CleanupError('CLEANUP_UNSAFE', 'Cleanup catalog image integrity failed.')
        json.loads(content)
        atomic_bytes(path, content)
    elif path.exists():
        path.unlink()
        _sync_directory(path.parent)


def target_paths(root, journal, index):
    """
    Independently validate journal source ownership and derive its fixed quarantine destination.

    Args:
        root: Trusted application storage root.
        journal: Bounded validated confirmed operation record.
        index: Position in the server-authored target list.
    Returns:
        Checked original source and private target-index staging path.
    Raises:
        CleanupError: A target is not a supported selected-map snapshot/CAS root.
    """
    target = journal['targets'][index]
    relative = target['path']
    if not target_allowed(relative):
        raise CleanupError('CLEANUP_UNSAFE', 'Cleanup journal contains an unrecognized target.')
    parent = Path(relative).parent
    valid_parents = roots(root, target['kind'], target['id'])
    if root / parent not in valid_parents or {'kind': target['kind'], 'id': target['id']} not in journal['selections']:
        raise CleanupError('CLEANUP_UNSAFE', 'Cleanup target is outside the confirmed map scope.')
    source = checked_path(root, *Path(relative).parts)
    staged = checked_path(root, AREA, journal['planId'], 'target-' + str(index))
    return source, staged


def rollback(root, journal):
    """
    Restore precommit catalogs and exact renamed targets without overwriting external changes.

    Args:
        root: Trusted application storage root.
        journal: Durable preparing/staged operation with both before/after catalog images.
    Returns:
        None after rollback is durably recorded as failed/not-cleared.
    Raises:
        CleanupError: Conflicting catalog/target identity requires explicit recovery, never overwrite.
        OSError: Restoration failed; original quarantined files remain owned by this operation.
    """
    for relative in CATALOGS:
        current = catalog_image(root, relative)
        if current['hash'] not in {journal['before'][relative]['hash'], journal['after'][relative]['hash']}:
            raise CleanupError('CLEANUP_RECOVERY_REQUIRED', 'A catalog changed outside cleanup; automatic rollback was stopped.')
    for index in reversed(range(len(journal['targets']))):
        source, staged = target_paths(root, journal, index)
        if staged.exists():
            if source.exists() or stamp(staged)[:2] != journal['targets'][index]['identity'][:2]:
                raise CleanupError('CLEANUP_RECOVERY_REQUIRED', 'A staged target conflicts with a newer storage object.')
            staged.rename(source)
            _sync_directory(source.parent)
    for relative in CATALOGS:
        publish_catalog(root, relative, journal['before'][relative])
    journal['phase'] = 'failed'
    journal['public'].update(status='failed', mapsCleared=False, registrationRemoved=False, canRetry=False,
                             pendingPurgeCount=0, errorCodes=['CLEANUP_ROLLED_BACK'])
    save(root, journal)


def load(root, directory):
    """
    Read one bounded marked journal without accepting arbitrary operation paths or shapes.

    Args:
        root: Trusted application storage root.
        directory: Direct operation child of the marked journal area.
    Returns:
        Validated JSON journal descriptor.
    Raises:
        CleanupError: Corrupt or unrecognized journal content/path must not drive filesystem writes.
    """
    checked = operation_directory(root, directory.name)
    path = checked_path(root, *checked.relative_to(root).parts, 'journal.json', directory=False)
    try:
        if not path.is_file() or path.stat().st_size > MAX_JOURNAL_BYTES:
            raise ValueError('missing journal')
        journal = json.loads(path.read_bytes())
        if journal.get('schemaVersion') != SCHEMA or journal.get('planId') != directory.name:
            raise ValueError('invalid journal')
        if set(journal.get('before', {})) != set(CATALOGS) or set(journal.get('after', {})) != set(CATALOGS):
            raise ValueError('invalid catalogs')
        if not isinstance(journal.get('targets'), list) or len(journal['targets']) > 200000:
            raise ValueError('invalid targets')
        action = journal.get('action', 'clear-snapshots')
        if action not in {'clear-snapshots', 'forget-registration', 'delete-map'}:
            raise ValueError('invalid action')
        if journal.get('public', {}).get('action', action) != action:
            raise ValueError('conflicting public action')
        if action == 'forget-registration' and journal['targets']:
            raise ValueError('metadata action cannot have physical targets')
        for index in range(len(journal['targets'])):
            target_paths(root, journal, index)
        return journal
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise CleanupError('CLEANUP_RECOVERY_REQUIRED', 'Cleanup journal cannot be safely recovered.') from error


def recover(root):
    """
    Roll back interrupted precommit operations before stores load; committed purge remains separate.

    Args:
        root: Explicit application data root, protected by the existing server process lock.
    Returns:
        Validated terminal/committed records for status lookup and purge resumption.
    Raises:
        CleanupError: Unknown or conflicting recovery state; no broad cleanup fallback is attempted.
    """
    root = Path(root).resolve()
    area = journal_area(root)
    if area is None:
        return []
    journals = []
    for directory in sorted(area.iterdir()):
        if directory.name == 'owner.json':
            continue
        if not re.fullmatch(r'[a-f0-9]{32}', directory.name) or not directory.is_dir():
            raise CleanupError('CLEANUP_RECOVERY_REQUIRED', 'Unrecognized cleanup journal content was preserved.')
        journal = load(root, directory)
        if journal['phase'] in {'preparing', 'staged', 'rollback-failed'}:
            rollback(root, journal)
        elif journal['phase'] not in {'committed', 'completed', 'partial', 'failed'}:
            raise CleanupError('CLEANUP_RECOVERY_REQUIRED', 'Unknown cleanup recovery phase.')
        journals.append(journal)
    return journals
