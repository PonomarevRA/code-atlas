"""
Fail-closed map ownership inventory; no client or registry path becomes a deletion target.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import time

from .map_storage import checked_path, identifier

MAX_ENTRIES = 200000
MAX_SCAN_SECONDS = 10
SNAPSHOT = re.compile(r'[a-f0-9]{32}')
OBJECT = re.compile(r'[a-f0-9]{64}\.json\.gz')
CODE_FILES = {'atlas-graph.json', 'mcp-graph.json', 'semantic-overlay.json', 'semantic-status.json', 'code-health-overlay.json', 'code-health-refresh.json'}
GROUPED = re.compile(r'atlas-grouped-(?:providers-v[0-9]+-[a-z0-9_.-]+|louvain-v[0-9]+)\.json')
SEMANTIC = re.compile(r'mcp-semantic-[a-f0-9]{16,64}\.json')
VENDOR_FILES = {'graph.json', 'manifest.json', 'atlas-chunks.json', '.graphify_root', '.cloud-cta-shown', '.graphify_analysis.json'}
PRESERVED = ['source-repositories', 'managed-source-clones', 'project-and-data-map-registration',
             'authored-code-and-data-views', 'document-maps', 'research', 'models', 'settings', 'backups', 'provider-cache']


class CleanupError(RuntimeError):
    """
    Public stable cleanup failure, independent of filesystem paths or secret catalog values.
    """
    def __init__(self, code, message, status=409, *, retryable=False, reason=None):
        """
        Attach a stable transport code without exposing an internal path.

        Args:
            code: Fixed public failure classification.
            message: Human-readable safe explanation.
            status: HTTP status, default409 for stale/unsafe/busy conditions.
            retryable: Whether the same unchanged authorization may be retried.
            reason: Optional fixed public diagnostic; unknown values are omitted rather than disclosed.
        """
        super().__init__(message)
        self.code, self.status, self.retryable = code, status, retryable
        self.reason = reason if reason in {'unrecognized-snapshot-file', 'unrecognized-snapshot-directory'} else None


def digest(value):
    """
    Hash a deterministic bounded server-owned descriptor.

    Args:
        value: JSON-compatible inventory/catalog identity, never an executable value.
    Returns:
        SHA256 digest used to compare preview and confirmation identities.
    """
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def stamp(path):
    """
    Reject redirected, hard-linked or special map objects and capture their exact stat identity.

    Args:
        path: Server-derived path below a checked storage root.
    Returns:
        Device/inode/mode/size/time identity; no source content is read.
    Raises:
        CleanupError: Unsafe object type, link, missing path or failed inspection.
    """
    try:
        value = path.lstat()
        junction = hasattr(path, 'is_junction') and path.is_junction()
        if stat.S_ISLNK(value.st_mode) or junction:
            raise ValueError('redirected')
        if not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode)):
            raise ValueError('special')
        if stat.S_ISREG(value.st_mode) and value.st_nlink != 1:
            raise ValueError('shared hard link')
        return [value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns]
    except (ValueError, OSError) as error:
        raise CleanupError('CLEANUP_UNSAFE', 'Map storage contains an unsafe or unavailable object.') from error


def roots(root, kind, map_id):
    """
    Derive only fixed supported map layouts from a validated registered catalog ID.

    Args:
        root: Application-owned data root.
        kind: Public code/data map kind.
        map_id: Existing registered ID, never a path supplied by a client.
    Returns:
        All canonical/legacy roots for this map, without creating directories.
    Raises:
        CleanupError: Invalid ID or redirected/conflicting managed path.
    """
    try:
        identifier(map_id)
        parts = ((map_id,), ('projects', map_id), ('maps', 'projects', map_id)) if kind == 'code' else (
            ('data-maps', map_id), ('maps', 'data-maps', map_id))
        return [checked_path(root, *item) for item in parts]
    except (ValueError, OSError) as error:
        raise CleanupError('CLEANUP_UNSAFE', 'Map storage layout or identity is unsafe.') from error


def target_allowed(relative):
    """
    Recognize a complete managed snapshot/CAS target independently of journal-controlled paths.

    Args:
        relative: Storage-relative server target restored from a durable journal.
    Returns:
        True only for a fixed project/data layout and a snapshot, staging or project-local CAS root.
    """
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        return False
    parts = path.parts
    if len(parts) == 2:
        kind, map_id, leaf = 'code', parts[0], parts[1]
    elif len(parts) == 3 and parts[0] in {'projects', 'data-maps'}:
        kind, map_id, leaf = ('data' if parts[0] == 'data-maps' else 'code'), parts[1], parts[2]
    elif len(parts) == 4 and parts[:2] in {('maps', 'projects'), ('maps', 'data-maps')}:
        kind, map_id, leaf = ('data' if parts[1] == 'data-maps' else 'code'), parts[2], parts[3]
    else:
        return False
    try:
        identifier(map_id)
    except ValueError:
        return False
    if SNAPSHOT.fullmatch(leaf):
        return True
    if kind == 'code' and leaf == '_objects':
        return True
    return kind == 'data' and bool(re.fullmatch(r'\.staging-[a-f0-9]{32}', leaf))


def _leaf_allowed(relative, kind, category):
    """
    Recognize known generated leaves without accepting authored files by extension alone.

    Args:
        relative: Path below one already-owned snapshot or CAS target.
        kind: Code/data map kind.
        category: snapshot or objects, selected by the server-owned root name.
    Returns:
        True for a generated leaf supported by this Atlas storage implementation.
    """
    parts, name = relative.parts, relative.name
    if category == 'objects':
        return len(parts) == 2 and bool(re.fullmatch(r'[a-f0-9]{2}', parts[0])) and bool(OBJECT.fullmatch(name)) and name.startswith(parts[0])
    if kind == 'data':
        if len(parts) == 1:
            return name in {'metadata.json', 'records.sqlite', 'records.sqlite-wal', 'records.sqlite-shm', 'records.sqlite-journal'}
        return len(parts) == 2 and parts[0] == 'sources' and bool(re.fullmatch(r'[0-9]+\.(csv|json|feature)', name))
    if len(parts) == 1:
        return name in CODE_FILES or bool(GROUPED.fullmatch(name)) or bool(SEMANTIC.fullmatch(name))
    if parts[0] == 'graphify-out':
        if len(parts) == 2:
            return name in VENDOR_FILES
        if len(parts) >= 3 and parts[1] == 'cache':
            return name == 'stat-index.json' or bool(re.fullmatch(r'[a-f0-9]{64}\.json', name))
    return False


def _directory_allowed(relative, kind, category):
    """
    Recognize generated container directories instead of deleting unknown empty authored folders.

    Args:
        relative: Directory path below one complete snapshot/CAS target; dot is the target itself.
        kind: Code/data storage kind.
        category: Snapshot or project-local object-store namespace.
    Returns:
        True only for explicit generated container roles.
    """
    parts = relative.parts
    if not parts:
        return True
    if category == 'objects':
        return len(parts) == 1 and bool(re.fullmatch(r'[a-f0-9]{2}', parts[0]))
    if kind == 'data':
        return parts == ('sources',)
    if parts in {('graphify-out',), ('graphify-out', 'cache'), ('graphify-out', 'cache', 'ast')}:
        return True
    return len(parts) == 4 and parts[:3] == ('graphify-out', 'cache', 'ast') and bool(re.fullmatch(r'v[A-Za-z0-9_.-]{1,128}', parts[3]))


def _tree(path, root, kind, category, records, started):
    """
    Inventory every deletion leaf with bounded traversal, refusing unknown content and redirects.

    Args:
        path: One server-derived complete target.
        root: Trusted application storage root used only for relative identity.
        kind: Code/data map kind.
        category: snapshot or objects allowlist.
        records: Shared exact inventory list, charged across the complete selection.
        started: Monotonic beginning of this bounded selection inventory.
    Returns:
        Sum of regular-file logical bytes, not allocated/reclaimed filesystem blocks.
    Raises:
        CleanupError: Unknown content, links, special files or inventory budget overflow.
    """
    size = 0
    pending = [path]
    while pending:
        current = pending.pop()
        if len(records) >= MAX_ENTRIES or time.monotonic() - started > MAX_SCAN_SECONDS:
            raise CleanupError('CLEANUP_LIMIT', 'Map inventory exceeds the bounded cleanup preview budget.')
        identity = stamp(current)
        records.append([current.relative_to(root).as_posix(), identity])
        if identity[0] != root.stat().st_dev:
            raise CleanupError('CLEANUP_UNSAFE', 'Map storage crosses a filesystem ownership boundary.')
        if stat.S_ISDIR(identity[2]):
            if not _directory_allowed(current.relative_to(path), kind, category):
                raise CleanupError('CLEANUP_UNSAFE', 'A snapshot contains an unrecognized directory.',
                                   reason='unrecognized-snapshot-directory')
            pending.extend(sorted(current.iterdir(), reverse=True))
        else:
            if not _leaf_allowed(current.relative_to(path), kind, category):
                raise CleanupError('CLEANUP_UNSAFE', 'A selected snapshot contains unrecognized files; nothing was cleared.',
                                   reason='unrecognized-snapshot-file')
            size += identity[3]
    return size


def _catalog_paths(projects):
    """
    Enumerate registered code snapshot pointers only for overlap validation, never target selection.

    Args:
        projects: Complete trusted ProjectStore catalog copy.
    Yields:
        Registered owner ID and internal pointer string for foreign-overlap rejection.
    """
    for project_id, item in projects.items():
        for field in ('_graph', '_rawGraph', '_mcpGraph'):
            if item.get(field):
                yield project_id, Path(item[field])


def _snapshot_pointers(root, map_id, row, records):
    """
    Validate known code pointer roles even when their published snapshot has disappeared.

    Args:
        root: Trusted storage directory; pointer values never select deletion targets.
        map_id: Registered owner whose fixed layouts alone are accepted.
        row: Copied project catalog entry containing optional private graph pointers.
        records: Inventory identities extended with missing/existing pointer leaves for stale fencing.
    Returns:
        Number of absent recognized pointer leaves, including optional lazy MCP files.
    Raises:
        CleanupError: Foreign layouts, wrong leaf names, links or noncanonical paths remain unsafe.
    """
    missing = 0
    suffixes = {'_graph': ('atlas-graph.json',), '_mcpGraph': ('mcp-graph.json',),
                '_rawGraph': ('graphify-out', 'graph.json')}
    owned = roots(root, 'code', map_id)
    for field, suffix in suffixes.items():
        if not row.get(field):
            continue
        pointer = Path(row[field])
        if not pointer.is_absolute() or '..' in pointer.parts:
            raise CleanupError('CLEANUP_UNSAFE', 'Registered snapshot pointer is not a canonical absolute path.')
        recognized = False
        for directory in owned:
            if not pointer.is_relative_to(directory):
                continue
            parts = pointer.relative_to(directory).parts
            recognized = len(parts) == 1 + len(suffix) and bool(SNAPSHOT.fullmatch(parts[0])) and parts[1:] == suffix
            if recognized:
                break
        if not recognized:
            raise CleanupError('CLEANUP_UNSAFE', 'Registered snapshot pointer is outside recognized map ownership.')
        try:
            checked_path(root, *pointer.relative_to(root).parts, directory=False)
        except (ValueError, OSError) as error:
            raise CleanupError('CLEANUP_UNSAFE', 'Registered snapshot pointer traverses unsafe storage.') from error
        exists = pointer.exists()
        records.append([pointer.relative_to(root).as_posix(), stamp(pointer) if exists else None])
        missing += int(not exists)
    return missing


def registration_inventory(projects, data_maps, selections):
    """
    Describe metadata-only removal without inspecting any map, source or authored-view path.

    Args:
        projects: Complete copied code catalog, used only as registration metadata.
        data_maps: Complete copied data catalog.
        selections: Validated existing kind/id pairs; no path or physical deletion authority.
    Returns:
        Catalog-bound descriptor with empty physical targets and zero removable bytes.
    Raises:
        CleanupError: A selected registration is absent or does not match its catalog key.
    """
    public = []
    for selection in selections:
        kind, map_id = selection['kind'], selection['id']
        catalog = projects if kind == 'code' else data_maps
        if map_id not in catalog or catalog[map_id].get('id') != map_id:
            raise CleanupError('CLEANUP_MAP_MISSING', 'Selected map no longer exists.', 404)
        row = catalog[map_id]
        pointer = row.get('_graph')
        snapshot_id = Path(pointer).parent.name if isinstance(pointer, str) else row.get('snapshotId')
        if not isinstance(snapshot_id, str) or not SNAPSHOT.fullmatch(snapshot_id):
            snapshot_id = None
        public.append({'kind': kind, 'id': map_id, 'title': row.get('name' if kind == 'code' else 'title', map_id),
            'snapshotId': snapshot_id,
            'builtAt': row.get('updatedAt' if kind == 'code' else 'builtAt'), 'sizeBytes': 0,
            'logicalBytes': 0, 'analysisVersion': None, 'versionStatus': 'unknown', 'snapshotCount': None})
    result = {'maps': public, 'targets': [], 'records': [], 'warnings': [],
              'catalogIdentity': digest([projects, data_maps])}
    result['fingerprint'] = digest(result)
    return result


def inventory(root, projects, data_maps, selections, *, protected_roots=()):
    """
    Bind a bounded selected code/data inventory to catalog identity and all managed layouts.

    Args:
        root: Trusted application data root, never derived from a request or catalog pointer.
        projects: Complete ProjectStore registry copied under its lock.
        data_maps: Complete data-map registry copied under its lock.
        selections: At most50 validated kind/id pairs from the public request.
        protected_roots: Additional authored/document/source roots requiring overlap exclusion.
    Returns:
        Internal exact inventory and public per-map consequence metadata; no mutation occurs.
    Raises:
        CleanupError: Missing IDs, unsafe ownership/links/content, foreign overlap or inventory limits.
    """
    root = Path(root).resolve()
    records, targets, public, warnings = [], [], [], []
    started = time.monotonic()
    sources = [Path(item['path']) for item in projects.values() if item.get('path')]
    sources.extend(Path(item['root']) for item in data_maps.values() if item.get('root'))
    sources.extend(Path(path) for path in protected_roots)
    ownership = []
    for registered_kind, catalog in (('code', projects), ('data', data_maps)):
        for registered_id in catalog:
            for directory in roots(root, registered_kind, registered_id):
                ownership.append((registered_kind, registered_id, directory))
    for selection in selections:
        kind, map_id = selection['kind'], selection['id']
        catalog = projects if kind == 'code' else data_maps
        if map_id not in catalog or catalog[map_id].get('id') != map_id:
            raise CleanupError('CLEANUP_MAP_MISSING', 'Selected map no longer exists.', 404)
        row, size, snapshots = catalog[map_id], 0, 0
        current_targets = []
        for directory in roots(root, kind, map_id):
            if not directory.exists():
                records.append([directory.relative_to(root).as_posix(), None])
                continue
            records.append([directory.relative_to(root).as_posix(), stamp(directory)])
            for child in sorted(directory.iterdir()):
                child_stamp = stamp(child)
                records.append([child.relative_to(root).as_posix(), child_stamp])
                recognized = bool(SNAPSHOT.fullmatch(child.name)) or (kind == 'code' and child.name == '_objects')
                recognized |= kind == 'data' and bool(re.fullmatch(r'\.staging-[a-f0-9]{32}', child.name))
                if not recognized:
                    if child.name not in {'views', '_provider-cache'}:
                        warnings.append('Unrecognized non-map material was preserved for ' + kind + ':' + map_id)
                    continue
                if not stat.S_ISDIR(child_stamp[2]):
                    raise CleanupError('CLEANUP_UNSAFE', 'Managed snapshot/CAS name conflicts with a non-directory object.')
                for source in sources:
                    absolute = source.resolve()
                    if child == absolute or child.is_relative_to(absolute) or absolute.is_relative_to(child):
                        raise CleanupError('CLEANUP_UNSAFE', 'Selected storage overlaps a registered source or authored scope.')
                for foreign_kind, foreign_id, owned_root in ownership:
                    if (foreign_kind, foreign_id) == (kind, map_id):
                        continue
                    if child == owned_root or child.is_relative_to(owned_root) or owned_root.is_relative_to(child):
                        raise CleanupError('CLEANUP_UNSAFE', 'Selected storage overlaps another registered map.')
                for foreign_id, pointer in _catalog_paths(projects):
                    if foreign_id != map_id and pointer.is_absolute() and pointer.resolve().is_relative_to(child):
                        raise CleanupError('CLEANUP_UNSAFE', 'Another registered project references selected map storage.')
                category = 'objects' if child.name == '_objects' else 'snapshot'
                first_record = len(records)
                target_size = _tree(child, root, kind, category, records, started)
                child_relative = child.relative_to(root)
                entries = [[Path(name).relative_to(child_relative).as_posix(), identity]
                           for name, identity in records[first_record:]]
                size += target_size
                relative = child.relative_to(root).as_posix()
                current_targets.append({'path': relative, 'identity': child_stamp, 'kind': kind, 'id': map_id, 'sizeBytes': target_size, 'entries': entries})
                snapshots += int(bool(SNAPSHOT.fullmatch(child.name)))
        # Absent known pointers can be reset, but never introduce a new physical deletion target.
        missing_pointers = 0
        if kind == 'code':
            missing_pointers = _snapshot_pointers(root, map_id, row, records)
        targets.extend(current_targets)
        public.append({'kind': kind, 'id': map_id, 'title': row.get('name' if kind == 'code' else 'title', map_id),
            'snapshotId': (Path(row['_graph']).parent.name if row.get('_graph') else row.get('snapshotId')),
            'builtAt': row.get('updatedAt' if kind == 'code' else 'builtAt'), 'sizeBytes': size,
            'logicalBytes': size, 'analysisVersion': None, 'versionStatus': 'unknown', 'snapshotCount': snapshots,
            'missingSnapshotPointers': missing_pointers})
    paths = [target['path'] for target in targets]
    if len(paths) != len(set(paths)):
        raise CleanupError('CLEANUP_UNSAFE', 'Selections contain overlapping storage ownership.')
    result = {'maps': public, 'targets': targets, 'records': sorted(records), 'warnings': sorted(set(warnings)),
              'catalogIdentity': digest([projects, data_maps])}
    result['fingerprint'] = digest(result)
    return result
