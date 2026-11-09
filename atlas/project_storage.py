"""Disk layout and offline removal of regenerable project caches, never snapshots."""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import shutil

from .process_lock import ProcessLock
from .map_storage import checked_path, identifier

_PROJECT_ID = re.compile(r'[a-f0-9]{16}')
_SNAPSHOT_ID = re.compile(r'[a-f0-9]{32}')
_GROUPING_CACHE = re.compile(r'atlas-grouped-(?:providers-v[0-9]+-[a-z0-9_.-]+|louvain-v[0-9]+)\.json')


def snapshot_directory(data_dir: Path, project_id: str, snapshot_id: str) -> Path:
    """New builds live under maps/projects; registry paths remain valid without migration."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', project_id) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError('Invalid project or snapshot identifier')
    root = Path(data_dir).resolve()
    destination = checked_path(root, 'maps', 'projects', project_id, snapshot_id)
    if not _contained(destination, root):
        raise ValueError('Project storage must not traverse symbolic links')
    return destination


def _contained(path: Path, root: Path) -> bool:
    """Reject symlink/junction traversal before any measurement or cache removal."""
    try:
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                return False
        return path.resolve().is_relative_to(root.resolve())
    except (ValueError, OSError):
        return False


def _size(path: Path) -> int:
    """Measure regular files without following links; allocated space can differ on cloned filesystems."""
    if path.is_file():
        return path.stat().st_size
    size = 0
    for directory, children, files in os.walk(path, followlinks=False):
        parent = Path(directory)
        children[:] = [name for name in children if _contained(parent / name, path)]
        for name in files:
            item = parent / name
            if _contained(item, path) and item.is_file():
                size += item.stat().st_size
    return size


def project_directories(root: Path, project_id: str) -> tuple[Path, ...]:
    """Return only this registered project's managed roots in all supported layouts."""
    identifier(project_id)
    return tuple(checked_path(root, *parts) for parts in ((project_id,), ("projects", project_id),
                                                       ("maps", "projects", project_id)))


def _snapshots(root: Path, projects: dict):
    """Inspect only registered IDs in legacy and new layouts; unknown directories are untouched."""
    for project_id, project in projects.items():
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            continue
        current = Path(project.get('_graph', '')).parent
        for parent in (root / project_id, root / 'projects' / project_id, root / 'maps' / 'projects' / project_id):
            if not _contained(parent, root) or not parent.is_dir():
                continue
            for snapshot in sorted(parent.iterdir()):
                if (_SNAPSHOT_ID.fullmatch(snapshot.name) and _contained(snapshot, root)
                        and snapshot.is_dir()):
                    yield snapshot, snapshot.resolve() == current.resolve()


def _cache_candidates(root: Path, projects: dict):
    """Extraction caches belong to finished workers; keep current grouping for fast reopening."""
    for snapshot, current in _snapshots(root, projects):
        cache = snapshot / 'graphify-out' / 'cache'
        if _contained(cache, root) and cache.is_dir():
            yield cache, 'extraction-cache'
        if not current:
            for path in sorted(snapshot.iterdir()):
                if _GROUPING_CACHE.fullmatch(path.name) and _contained(path, root) and path.is_file():
                    yield path, 'historical-grouping-cache'


def storage_report(data_dir: Path) -> dict:
    """Return a read-only inventory; a report alone never authorizes deletion of arbitrary files."""
    root = Path(data_dir).resolve()
    registry = root / 'projects.json'
    if not _contained(registry, root):
        raise RuntimeError('Project registry must not be a symbolic link')
    projects = json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {}
    if not isinstance(projects, dict) or any(not isinstance(row, dict) for row in projects.values()):
        raise RuntimeError('Invalid project registry; storage was not changed')
    candidates = []
    for path, kind in _cache_candidates(root, projects):
        candidates.append({'path': path.relative_to(root).as_posix(), 'kind': kind,
                           'bytes': _size(path)})
    return {'dataDirectory': str(root), 'newProjectsDirectory': str(root / 'maps' / 'projects'),
            'projectCount': len(projects), 'snapshotCount': sum(1 for _ in _snapshots(root, projects)),
            'totalBytes': _size(root), 'reclaimableBytes': sum(row['bytes'] for row in candidates),
            'candidates': candidates,
            'preserved': ['source-repositories', 'snapshots', 'raw-graphs', 'mcp-projections',
                          'saved-views', 'health-records', 'research', 'models', 'current-grouping']}


def discard_extraction_cache(snapshot: Path) -> None:
    """Remove only a completed worker's private AST cache; no later reader uses this extraction cache."""
    snapshot = Path(snapshot).resolve()
    cache = snapshot / 'graphify-out' / 'cache'
    if _contained(cache, snapshot) and cache.is_dir():
        shutil.rmtree(cache)


@contextmanager
def temporary_extraction_cache(snapshot: Path):
    """Clean a private cache after the blocking extractor exits, before publishing a terminal job state."""
    try:
        yield
    finally:
        try:
            discard_extraction_cache(snapshot)
        except OSError:
            # Disk cleanup cannot hide extraction failure or invalidate primary graph facts.
            logging.getLogger(__name__).exception('Extraction cache cleanup failed')


def optimize_storage(data_dir: Path) -> dict:
    """Require an offline process lock and recheck every fixed cache target before removing it.

    Snapshot history and exported MCP paths remain intact. Failure may leave some
    caches already removed; they are rebuildable and no primary data is changed.
    """
    root = Path(data_dir).resolve()
    if not root.is_dir():
        raise RuntimeError('Atlas data directory does not exist')
    with ProcessLock(root / 'server.lock'):
        report = storage_report(root)
        removed_bytes, removed, errors = 0, 0, []
        for item in report['candidates']:
            path = root / item['path']
            if not _contained(path, root):
                errors.append(item['path'])
                continue
            try:
                if item['kind'] == 'extraction-cache':
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed_bytes += item['bytes']
                removed += 1
            except OSError:
                errors.append(item['path'])
        return {**report, 'removedBytes': removed_bytes, 'removedTargets': removed,
                'errors': errors, 'status': 'partial' if errors else 'completed'}
