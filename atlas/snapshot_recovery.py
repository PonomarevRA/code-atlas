"""Bounded repair on explicit current-map use; pinned/history/status readers stay read only."""
from pathlib import Path
import os
import re
from .map_storage import checked_path
from .project_storage import project_directories
from .store import StoreError, write_json


class SnapshotRecoveryPending(StoreError):
    """Expose recovery state and an optional already-owned build job without source paths."""
    def __init__(self, message, code, job_id=None):
        """
        Bind a safe user message to a fixed recovery code and optional current job ID.

        Args:
            message: Localized explanation of recovery progress or an explicit next action.
            code: Fixed machine-readable recovery state.
            job_id: Existing server-issued job identity, never a client path.
        """
        super().__init__(message, 409)
        self.code, self.job_id = code, job_id


def _owned_pointer(store, project_id, value):
    """
    Validate a canonical current graph pointer without accepting symlink traversal.

    Args:
        store: ProjectStore owning the explicit data root.
        project_id: Existing registered project identity.
        value: Stored current graph pointer; never a deletion target.
    Returns:
        Checked graph path, or None for malformed/foreign/redirected storage.
    """
    try:
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts or path.name != 'atlas-graph.json':
            return None
        if not re.fullmatch(r'[a-f0-9]{32}', path.parent.name):
            return None
        if path.parent.parent not in project_directories(store.data_dir, project_id):
            return None
        return checked_path(store.data_dir, *path.relative_to(store.data_dir).parts, directory=False)
    except (OSError, ValueError, TypeError):
        return None


def ensure_current_snapshot(store, project_id):
    """
    Queue one repair per missing published pointer when the user opens/searches the current map.

    Args:
        store: Admitted ProjectStore; its lock and guarded executor own publication lifetime.
        project_id: Current project selected explicitly through graph or unpinned search.
    Returns:
        None for an existing snapshot or deliberately unbuilt registration.
    Raises:
        SnapshotRecoveryPending: Started/running/busy/failed recovery or unavailable source.
        StoreError: Unknown project or an original registry/executor write failure.
    """
    with store.lock:
        project = store._project(project_id)
        value = project.get('_graph')
        if not value:
            return  # Explicit cleanup/not-built is not an accidentally missing published revision.
        path = _owned_pointer(store, project_id, value)
        if path is None or path.is_file():
            return  # The existing reader reports unsafe pointers without authorizing recovery.
        if store.active_job:
            active = store.jobs.get(store.active_job, {})
            own = active.get('projectId') == project_id
            raise SnapshotRecoveryPending('Восстановление карты выполняется.' if own else 'Дождитесь завершения другой сборки карты.',
                                          'SNAPSHOT_REBUILD_RUNNING' if own else 'SNAPSHOT_REBUILD_BUSY',
                                          store.active_job if own else None)
        if project.get('_missingSnapshotRepair') == value:
            raise SnapshotRecoveryPending('Автоматическое восстановление уже выполнялось. Запустите повторную сборку явно.',
                                          'SNAPSHOT_REBUILD_ATTEMPTED')
        source = Path(project.get('path', ''))
        try:
            canonical = source.resolve()
            if not source.is_absolute() or canonical != source or canonical.is_relative_to(store.data_dir) or not source.is_dir():
                raise OSError('Unavailable source')
            with os.scandir(source) as entries:
                next(entries, None)  # Verify directory readability without executing analyzed code.
        except (OSError, RuntimeError, ValueError):
            raise SnapshotRecoveryPending('Исходная папка недоступна. Восстановите её или удалите запись карты из списка.',
                                          'SNAPSHOT_SOURCE_UNAVAILABLE') from None
        # Persist the attempt before queueing. A crash/failed job must not cause an automatic retry loop.
        project['_missingSnapshotRepair'] = value
        write_json(store.registry, store.projects)
        try:
            job = store._submit(project_id)
        except Exception:
            # An executor/storage failure must not leave a queued job blocking every later manual rebuild.
            active = store.jobs.get(store.active_job, {})
            if active.get('projectId') == project_id:
                active.update(status='failed', error='Не удалось запустить восстановление карты.')
                store.active_job = None
            project.update(status='failed', error='Не удалось запустить восстановление карты.')
            write_json(store.registry, store.projects)
            raise SnapshotRecoveryPending('Не удалось запустить восстановление. Повторите сборку явно.',
                                          'SNAPSHOT_REBUILD_ATTEMPTED') from None
        raise SnapshotRecoveryPending('Снимок отсутствует. Запущено восстановление карты.', 'SNAPSHOT_REBUILD_STARTED', job['id'])
