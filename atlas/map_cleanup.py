"""
Explicit preview/confirmation service for snapshot cleanup and separate metadata-only registration removal.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import base64
import json
import os
from pathlib import Path
from threading import RLock
import time
from uuid import uuid4

from .map_cleanup_inventory import CleanupError, PRESERVED, digest, inventory, registration_inventory, roots, stamp, SNAPSHOT
from . import map_cleanup_journal as journal_store
from .map_maintenance import MaintenanceBusy
from .map_cleanup_purge import purge_target

PLAN_TTL = 600
MAX_PLANS = 32
MAX_OPERATIONS = 256


def utc(timestamp):
    """
    Render an absolute UTC deadline for the UI without relying on its local clock.

    Args:
        timestamp: Unix seconds supplied by the service clock.
    Returns:
        ISO8601 UTC timestamp.
    """
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _image(value):
    """
    Prepare an exact after-catalog image using the same durable representation as rollback.

    Args:
        value: Complete copied reset registry, including unchanged other map records.
    Returns:
        Encoded catalog image with SHA256 integrity identity.
    """
    content = journal_store.encoded(value)
    from hashlib import sha256
    return {'exists': True, 'bytes': base64.b64encode(content).decode(), 'hash': sha256(content).hexdigest()}


def reset_catalogs(projects, data_maps, selections, *, action='clear-snapshots'):
    """
    Apply the confirmed catalog action without changing source files or authored-view storage.

    Args:
        projects: Complete copied code project registry; never mutated in place.
        data_maps: Complete copied data map registry; root/files/options/title remain unchanged.
        selections: Confirmed public kind/id pairs.
        action: Clear current snapshot state, forget metadata only, or delete both map snapshots and registration.
    Returns:
        Copied code/data registries with only the explicitly selected action applied.
    """
    code, data = deepcopy(projects), deepcopy(data_maps)
    for selected in selections:
        map_id = selected['id']
        if action in {'forget-registration', 'delete-map'}:
            (code if selected['kind'] == 'code' else data).pop(map_id)
            continue
        if selected['kind'] == 'code':
            item = code[map_id]
            for field in ('_graph', '_rawGraph', '_mcpGraph', '_mcpGraphLazy', 'updatedAt'):
                item.pop(field, None)
            item.update(status='not-built', nodeCount=0, edgeCount=0, error=None)
        else:
            data[map_id].update(status='not-built', snapshotId=None, builtAt=None, lastVerifiedAt=None,
                                jobId=None, error=None, records=0, nodeCount=0, edgeCount=0)
    return code, data


class MapCleanup:
    """
    Own bounded opaque preview plans and durable confirmed operations; purge never touches live paths.
    """
    def __init__(self, store, data_maps, maintenance, *, recovered=(), protected_roots=(), invalidate=None,
                 clock=time.time, fault=lambda phase: None):
        """
        Create an idle service and resume only previously confirmed committed quarantine purges.

        Args:
            store: Application ProjectStore, sharing the maintenance boundary.
            data_maps: Application DataMapStore, sharing the same boundary.
            maintenance: Shared HTTP/worker admission gate.
            recovered: Validated journals returned before either store was loaded.
            protected_roots: Callable returning additional authored/document source roots.
            invalidate: Optional trusted in-memory selected-project cache invalidation hook.
            clock: Unix-seconds clock, injectable for expiration tests.
            fault: Trusted phase hook for synthetic crash/failure tests; never request controlled.
        """
        self.store, self.data_maps, self.maintenance = store, data_maps, maintenance
        self.root, self.clock, self.fault = store.data_dir, clock, fault
        self.protected_roots, self.invalidate = protected_roots, invalidate
        self.lock, self.plans, self.operations, self.futures = RLock(), OrderedDict(), {}, {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='atlas-map-purge')
        self.closed = False
        for journal in recovered:
            self.operations[journal['planId']] = journal
            if journal['phase'] in {'committed', 'partial'}:
                self._queue_purge(journal)

    def _catalogs(self):
        """
        Copy registries under a fixed code-then-data lock order.

        Returns:
            Independent complete registry copies and exact on-disk canonical catalog images.
        """
        with self.store.lock, self.data_maps.lock:
            before = {name: journal_store.catalog_image(self.root, name) for name in journal_store.READ_CATALOGS}
            projects, data = deepcopy(self.store.projects), deepcopy(self.data_maps.maps)
            self._catalog_current(before['projects.json'], projects, 'code')
            authoritative = before['maps/data-maps/catalog.json']
            if not authoritative['exists']:
                authoritative = before['data-maps/catalog.json']
            self._catalog_current(authoritative, data, 'data')
            return projects, data, before

    def _catalog_current(self, image, expected, kind):
        """
        Refuse external catalog changes instead of overwriting records unknown to the running stores.

        Args:
            image: Exact authoritative on-disk catalog image or explicit absence.
            expected: Current in-memory registry copied under its service lock.
            kind: Code/data startup-recovery normalization rule.
        Raises:
            CleanupError: A catalog changed outside the running application and must be reloaded.
        """
        stored = json.loads(base64.b64decode(image['bytes'])) if image['exists'] else {}
        if isinstance(stored, dict):
            for item in stored.values():
                if isinstance(item, dict) and item.get('status') in {'queued', 'running'}:
                    item['status'] = 'failed'
                    item['error'] = ('Приложение остановилось во время сборки. Запустите её снова.' if kind == 'code'
                                     else 'Импорт был прерван. Обновите карту явно.')
        if stored != expected:
            raise CleanupError('CLEANUP_STALE', 'Map catalog changed outside Atlas; reload the application before cleanup.')

    def _inventory(self, projects, data, selections, action='clear-snapshots'):
        """
        Apply one ownership inventory policy to preview and execution without reading arbitrary paths.

        Args:
            projects: Copied complete code registry.
            data: Copied complete data registry.
            selections: Validated public selection pairs.
            action: Immutable preview mode; metadata-only forget must never walk map paths.
        Returns:
            Exact internal inventory with safe public per-map metadata.
        """
        if action == 'forget-registration':
            return registration_inventory(projects, data, selections)
        protected = self.protected_roots() if callable(self.protected_roots) else self.protected_roots
        return inventory(self.root, projects, data, selections, protected_roots=protected)

    def preview(self, selections, *, action='clear-snapshots'):
        """
        Create one bounded expiring authorization from existing catalog IDs, never a filesystem path.

        Args:
            selections: One to50 distinct kind/id pairs already parsed by the HTTP DTO.
            action: clear-snapshots by default; forget-registration is metadata only; delete-map combines both.
        Returns:
            Safe public plan with only selected-clearable logical byte totals and consequences.
        Raises:
            CleanupError: Missing/unsafe/empty maps, capacity, busy workers or closed service.
        """
        if action not in {'clear-snapshots', 'forget-registration', 'delete-map'}:
            raise CleanupError('CLEANUP_INVALID_ACTION', 'Unknown cleanup action.', 422)
        ordered = sorted(selections, key=lambda item: (item['kind'], item['id']))
        if not 1 <= len(ordered) <= 50 or len({(x['kind'], x['id']) for x in ordered}) != len(ordered):
            raise CleanupError('CLEANUP_INVALID_SELECTION', 'Select one to50 distinct maps.', 422)
        with self.maintenance.lock:
            if self.maintenance.workers or self.maintenance.committing or self.maintenance.degraded:
                raise CleanupError('CLEANUP_BUSY', 'Wait for active analysis jobs to finish.', retryable=True)
        projects, data, images = self._catalogs()
        captured = self._inventory(projects, data, ordered, action)
        if action == 'clear-snapshots' and not captured['targets'] and not any(
                item.get('missingSnapshotPointers') for item in captured['maps']):
            raise CleanupError('CLEANUP_EMPTY', 'Selected maps contain no clearable snapshots.')
        with self.lock:
            if self.closed:
                raise CleanupError('CLEANUP_BUSY', 'Map cleanup service is stopping.', 503)
            for key, plan in list(self.plans.items()):
                if plan['deadline'] <= self.clock():
                    del self.plans[key]
            if len(self.plans) >= MAX_PLANS or len(self.operations) >= MAX_OPERATIONS:
                raise CleanupError('CLEANUP_LIMIT', 'Cleanup plan/history capacity reached.')
            plan_id, deadline = uuid4().hex, self.clock() + PLAN_TTL
            public = {'schemaVersion': journal_store.SCHEMA, 'planId': plan_id, 'expiresAt': utc(deadline),
                'status': 'ready', 'action': action, 'maps': captured['maps'], 'mapsCleared': False,
                'registrationRemoved': False, 'removed': [],
                'pendingPurgeCount': 0, 'canRetry': False, 'errorCodes': [],
                'removedBytes': 0, 'pendingPurgeBytes': 0, 'purgeSizeStatus': 'not-started',
                'totals': {'mapCount': len(ordered), 'sizeBytes': sum(item['sizeBytes'] for item in captured['maps'])},
                'preserved': PRESERVED, 'warnings': captured['warnings'] + [
                    'All selected owned snapshot history and project-local CAS objects will be cleared.',
                    'Registrations and authored views remain; old snapshot links and externally issued Graphify paths can stop working.',
                    'Source repositories are not changed; retained registrations remain available for rebuilding.',
                    'Sizes are regular-file logical bytes, not guaranteed reclaimed filesystem allocation.']}
            if action == 'forget-registration':
                public['preserved'] = [item for item in PRESERVED if item != 'project-and-data-map-registration'] + ['all-map-snapshot-files']
                public['warnings'] = [
                    'Only selected registrations will be removed from the map catalog; no physical map files are deleted.',
                    'All sources, snapshots and authored saved views are retained; no disk space is reclaimed.',
                    'Current map actions require explicitly registering the source again. Historical saved views remain readable.']
            elif action == 'delete-map':
                public['preserved'] = [item for item in PRESERVED if item != 'project-and-data-map-registration']
                public['warnings'] = captured['warnings'] + [
                    'All selected owned snapshot history and project-local CAS objects will be cleared, and selected map registrations removed.',
                    'Source repositories and authored saved views are retained; current map actions require explicitly registering the source again.',
                    'Old snapshot links and externally issued Graphify paths can stop working.',
                    'Sizes are regular-file logical bytes; failed physical purge is reported separately from committed catalog removal.']
            self.plans[plan_id] = {'deadline': deadline, 'action': action, 'selections': ordered, 'inventory': captured,
                                   'catalogImages': images, 'public': public}
            return deepcopy(public)

    def status(self, plan_id):
        """
        Return the same operation after lost acknowledgement; status reads never trigger deletion.

        Args:
            plan_id: Opaque preview/confirmed-operation identity.
        Returns:
            Public ready/committed-purge/terminal operation, retaining preview consequences.
        Raises:
            CleanupError:404 for unknown plans or410 for an unconfirmed expired plan.
        """
        with self.lock:
            if plan_id in self.operations:
                return deepcopy(self.operations[plan_id]['public'])
            plan = self.plans.get(plan_id)
            if plan is None:
                raise CleanupError('CLEANUP_PLAN_MISSING', 'Cleanup plan was not found.', 404)
            if plan['deadline'] <= self.clock():
                raise CleanupError('CLEANUP_EXPIRED', 'Cleanup plan expired; request a new preview.', 410)
            return deepcopy(plan['public'])

    def execute(self, plan_id):
        """
        Confirm one unchanged plan, or idempotently retry only this operation's committed quarantine.

        Args:
            plan_id: Existing opaque plan identity; HTTP requires an explicit confirm:true body.
        Returns:
            Same durable operation status; physical purge proceeds outside the admission gate.
        Raises:
            CleanupError: Stale/busy/unsafe/expired plans change no live map data.
        """
        with self.lock:
            if self.closed:
                raise CleanupError('CLEANUP_BUSY', 'Map cleanup service is stopping.', 503)
            existing = self.operations.get(plan_id)
            if existing:
                if existing['public']['canRetry'] and plan_id not in self.futures:
                    self._queue_purge(existing)
                return deepcopy(existing['public'])
            self.status(plan_id)
            plan = self.plans[plan_id]
            try:
                with self.maintenance.exclusive():
                    operation = self._commit(plan_id, plan)
            except MaintenanceBusy as error:
                raise CleanupError('CLEANUP_BUSY', str(error), retryable=True) from error
            self.operations[plan_id] = operation
            self.plans.pop(plan_id, None)
            if operation['phase'] == 'committed':
                self._queue_purge(operation)
            return deepcopy(operation['public'])

    def _commit(self, plan_id, plan):
        """
        Revalidate and journal the action, then publish catalogs after any explicitly authorized staging.

        Args:
            plan_id: Confirmed opaque operation identity.
            plan: Exact preview descriptor; may not broaden its target set.
        Returns:
            Durable committed/completed operation, or a rolled-back terminal failed operation.
        Raises:
            CleanupError: Stale/unsafe conditions or rollback conflicts; newer data is never selected.
        """
        projects, data, images = self._catalogs()
        action = plan.get('action', 'clear-snapshots')
        current = self._inventory(projects, data, plan['selections'], action)
        if current['fingerprint'] != plan['inventory']['fingerprint'] or images != plan['catalogImages']:
            raise CleanupError('CLEANUP_STALE', 'Map storage changed; request a new preview.')
        if self.clock() >= plan['deadline']:
            raise CleanupError('CLEANUP_EXPIRED', 'Cleanup plan expired; request a new preview.', 410)
        code_after, data_after = reset_catalogs(projects, data, plan['selections'], action=action)
        canonical_images = {name: images[name] for name in journal_store.CATALOGS}
        after = dict(canonical_images)
        if any(item['kind'] == 'code' for item in plan['selections']):
            after['projects.json'] = _image(code_after)
        if any(item['kind'] == 'data' for item in plan['selections']):
            after['maps/data-maps/catalog.json'] = _image(data_after)
        operation = {'schemaVersion': journal_store.SCHEMA, 'planId': plan_id, 'phase': 'preparing', 'action': action,
            'selections': plan['selections'], 'targets': current['targets'], 'before': canonical_images, 'after': after,
            'public': deepcopy(plan['public'])}
        directory = journal_store.operation_directory(self.root, plan_id, create=True)
        try:
            journal_store.save(self.root, operation)
        except Exception as error:
            # No map/catalog mutation happened: retire only this newly-created private empty receipt.
            candidate = directory / 'journal.json'
            if candidate.is_file() and not candidate.is_symlink():
                candidate.unlink()
            directory.rmdir()
            if isinstance(error, CleanupError):
                raise
            raise CleanupError('CLEANUP_JOURNAL_FAILED', 'Cleanup could not publish its durable authorization; maps were not changed.', 503) from error
        try:
            self.fault('after_journal')
            for index, target in enumerate(operation['targets']):
                source, staged = journal_store.target_paths(self.root, operation, index)
                if stamp(source) != target['identity']:
                    raise CleanupError('CLEANUP_STALE', 'A map target changed before staging.')
                source.rename(staged)
                journal_store._sync_directory(source.parent)
                journal_store._sync_directory(directory)
                self.fault('after_rename:' + str(index))
            operation['phase'] = 'staged'
            journal_store.save(self.root, operation)
            for relative in journal_store.CATALOGS:
                journal_store.publish_catalog(self.root, relative, after[relative])
                self.fault('after_catalog:' + relative)
            operation['phase'] = 'committed'
            operation['public'].update(status='purging', mapsCleared=action in {'clear-snapshots', 'delete-map'},
                                        registrationRemoved=action in {'forget-registration', 'delete-map'}, removed=plan['selections'],
                                        pendingPurgeCount=len(operation['targets']), canRetry=False,
                                        removedBytes=None, pendingPurgeBytes=None, purgeSizeStatus='in-progress')
            journal_store.save(self.root, operation)
            if not operation['targets']:
                # No worker is started for metadata-only transactions or missing-snapshot resets.
                operation['phase'] = 'completed'
                operation['public'].update(status='completed', removedBytes=0, pendingPurgeBytes=0,
                                            purgeSizeStatus='completed-logical')
                journal_store.save(self.root, operation)
        except Exception:
            try:
                journal_store.rollback(self.root, operation)
            except Exception as error:
                self.maintenance.degraded = True
                operation['phase'] = 'rollback-failed'
                operation['public'].update(status='failed', mapsCleared=False, registrationRemoved=False, canRetry=False,
                                            errorCodes=['CLEANUP_RECOVERY_REQUIRED'])
                journal_store.save(self.root, operation)
                raise CleanupError('CLEANUP_RECOVERY_REQUIRED', 'Cleanup rollback requires safe recovery; map access is paused.', 500) from error
            return operation
        with self.store.lock, self.data_maps.lock:
            self.store.projects = code_after
            self.data_maps.maps = data_after
            if any(item['kind'] == 'data' for item in plan['selections']):
                self.data_maps.catalog_published = True
            for selected in plan['selections']:
                if selected['kind'] == 'code':
                    self.store._published_graph.pop(selected['id'], None)
            self.store._search_facts_key = self.store._search_facts = None
        if self.invalidate:
            self.invalidate([x['id'] for x in plan['selections'] if x['kind'] == 'code'])
        return operation

    def _queue_purge(self, operation):
        """
        Enqueue only this durable committed quarantine; this pool intentionally bypasses map admission.

        Args:
            operation: Confirmed committed/partial journal, never a fresh catalog selection.
        Returns:
            None; status remains queryable while independent API work resumes.
        """
        plan_id = operation['planId']
        with self.lock:
            if not operation['targets']:
                operation['phase'] = 'completed'
                operation['public'].update(status='completed', canRetry=False, pendingPurgeCount=0,
                    mapsCleared=operation.get('action', 'clear-snapshots') in {'clear-snapshots', 'delete-map'},
                    registrationRemoved=operation.get('action') in {'forget-registration', 'delete-map'},
                    removedBytes=0, pendingPurgeBytes=0, purgeSizeStatus='completed-logical')
                journal_store.save(self.root, operation)
                return
            operation['public'].update(status='purging', canRetry=False, removedBytes=None,
                                       pendingPurgeBytes=None, purgeSizeStatus='in-progress')
            future = self.executor.submit(self._purge, plan_id)
            self.futures[plan_id] = future

    def _validate_staged(self, staged, target):
        """
        Reject any added/replaced quarantine object; partial retries may only lose original entries.

        Args:
            staged: Checked private target-index directory already moved by this operation.
            target: Original server inventory including each relative entry and stat identity.
        Returns:
            None when every surviving object belongs to the confirmed original inventory.
        Raises:
            CleanupError: New links/files/directories or replaced original objects require safe review.
        """
        expected = {name: identity for name, identity in target['entries']}
        pending = [staged]
        while pending:
            current = pending.pop()
            name = current.relative_to(staged).as_posix()
            identity = stamp(current)
            original = expected.get(name)
            if original is None:
                raise CleanupError('CLEANUP_UNSAFE', 'Unrecognized material appeared in cleanup quarantine.')
            if current.is_dir():
                # Root rename and partial deletion change directory times; device/inode/type remain fixed.
                if identity[:3] != original[:3]:
                    raise CleanupError('CLEANUP_UNSAFE', 'A quarantine directory was replaced.')
                pending.extend(current.iterdir())
            elif identity != original:
                raise CleanupError('CLEANUP_UNSAFE', 'A quarantine file was replaced or modified.')

    def _purge(self, plan_id):
        """
        Remove only checked staged roots; failure is partial and retry cannot broaden original authority.

        Args:
            plan_id: Existing durable confirmed operation identity.
        Returns:
            None after persisting completed/partial physical-purge state.
        """
        operation = self.operations[plan_id]
        errors, pending = [], 0
        for index in range(len(operation['targets'])):
            try:
                _, staged = journal_store.target_paths(self.root, operation, index)
                if not staged.exists():
                    continue
                self._validate_staged(staged, operation['targets'][index])
                self.fault('before_purge:' + str(index))
                purge_target(self.root, staged, operation['targets'][index])
            except (OSError, CleanupError):
                errors.append('CLEANUP_PURGE_FAILED')
                pending += 1
        with self.lock:
            operation['phase'] = 'partial' if errors else 'completed'
            operation['public'].update(status=operation['phase'], mapsCleared=True, pendingPurgeCount=pending,
                                        canRetry=bool(errors), errorCodes=sorted(set(errors)),
                removedBytes=None if errors else sum(target['sizeBytes'] for target in operation['targets']),
                pendingPurgeBytes=None if errors else 0,
                purgeSizeStatus='unknown-partial' if errors else 'completed-logical')
            try:
                journal_store.save(self.root, operation)
            finally:
                self.futures.pop(plan_id, None)

    def pending(self):
        """
        Discover durable unfinished operations after reload without starting or retrying cleanup.

        Returns:
            At most32 pending public receipts and an explicit truncation indicator.
        """
        with self.lock:
            values = [deepcopy(operation['public']) for operation in self.operations.values()
                      if operation['phase'] in {'committed', 'partial', 'rollback-failed'}]
            values.sort(key=lambda item: item['planId'])
            return {'schemaVersion': journal_store.SCHEMA, 'operations': values[:32], 'truncated': len(values) > 32}

    def storage_status(self):
        """
        Explain quarantine bytes omitted from the ordinary live-map catalog totals.

        Returns:
            Pending physical-data flag/count, with unknown bytes until purge completion.
        """
        values = self.pending()
        count = len(values['operations'])
        return {'hasPendingPhysicalData': bool(count), 'pendingOperationCount': count,
                'pendingPurgeBytes': None if count else 0, 'truncated': values['truncated']}

    def availability(self, kind, map_id):
        """
        Offer snapshot reset/cleanup and a distinct metadata-only removal capability for catalog rows.

        Args:
            kind: Existing code/data catalog row kind.
            map_id: Existing catalog identity, not an arbitrary path.
        Returns:
            Additive UI capabilities; missing snapshots can be reset, while forget never grants file deletion.
        """
        metadata = {'forgetAvailable': True, 'forgetReason': 'metadata-only',
                    'deleteAvailable': True, 'deleteReason': 'preview-required', 'logicalBytes': None,
                    'analysisVersion': None, 'versionStatus': 'unknown'}
        try:
            available = False
            for directory in roots(self.root, kind, map_id):
                if directory.exists():
                    for child in directory.iterdir():
                        stamp(child)
                        if SNAPSHOT.fullmatch(child.name) or (kind == 'code' and child.name == '_objects'):
                            available = True
            missing = False
            if kind == 'code':
                with self.store.lock:
                    row = deepcopy(self.store.projects.get(map_id, {}))
                from .map_cleanup_inventory import _snapshot_pointers
                _snapshot_pointers(self.root, map_id, row, [])
                missing = bool(row.get('_graph') and not Path(row['_graph']).exists())
            return {**metadata, 'available': available or missing,
                    'reason': 'missing-snapshot' if missing else ('preview-required' if available else 'no-clearable-snapshots')}
        except (OSError, CleanupError):
            return {**metadata, 'available': False, 'reason': 'unsafe-storage'}

    def close(self):
        """
        Stop new confirmations and wait only for this service's own quarantine purger.

        Returns:
            None; interrupted process termination is recoverable from the confirmed durable journal.
        """
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True)
