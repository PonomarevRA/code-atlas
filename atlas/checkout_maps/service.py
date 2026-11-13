"""Independent temporary checkout maps with sliding useful-read leases."""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from threading import Event, RLock, Thread
import time
from uuid import uuid4

from ..code_health_service import CodeHealthService
from ..integrations.graphify.runtime import GraphifyRuntime
from ..store import ProjectStore
from .errors import CheckoutMapError
from .models import CheckoutEntry, MARKER, MAX_SESSIONS, RECLAIM_IDLE_SECONDS, SCHEMA, TTL_SECONDS


class _CapturedRuntime(GraphifyRuntime):
    """Use the host's captured provider selection without reading canonical settings."""

    def __init__(self, data_dir, selected_python):
        super().__init__(data_dir)
        self.selected_python = selected_python

    def selected(self):
        """Build activation uses this fixed value rather than a mutable canonical config."""
        return self.selected_python


def _checkout_clock():
    """Count system sleep where the OS exposes a suspend-aware monotonic clock."""
    if sys.platform == 'darwin':
        return time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
    if hasattr(time, 'CLOCK_BOOTTIME'):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


def _utc(value):
    """Expose absolute UTC expiry while monotonic time governs live-session lifetime."""
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        raise CheckoutMapError('Invalid recorded UTC timestamp', 503, 'checkout_map_storage') from None


def _text(value, maximum, name):
    """Accept bounded display/owner declarations without claiming authenticated identity."""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise CheckoutMapError('Invalid '+name)
    return value


class CheckoutMaps:
    """Own only opaque private map folders, never source or canonical project state.

    Shutdown retains unexpired sessions/index for restart. Finish/expiry retires
    admissions immediately, but deletion waits for all useful-read leases and the
    private builder to drain. Status/list/ensure reuse never renew a session.
    """

    def __init__(self, data_dir, *, clock=_checkout_clock, wall_clock=time.time,
                 capacity=MAX_SESSIONS, runtime_python=None, store_factory=ProjectStore):
        """Restore marked sessions; provider selection is trusted host configuration.

        Injectable clocks/store factory support deterministic lifetime tests.
        Production always reuses the existing ProjectStore and CodeHealthService.
        """
        if type(capacity) is not int or not 1 <= capacity <= MAX_SESSIONS:
            raise CheckoutMapError('Capacity must be between one and eight')
        if runtime_python is not None and (not isinstance(runtime_python, str)
                or not Path(runtime_python).is_absolute() or len(runtime_python) > 4096):
            raise CheckoutMapError('Runtime selection must be an absolute host-configured path')
        self.clock, self.wall_clock = clock, wall_clock
        self.capacity, self.runtime_python, self.store_factory = capacity, runtime_python, store_factory
        self.lock, self.stop = RLock(), Event()
        self.thread, self.closed, self.entries = None, False, {}
        base = Path(data_dir).absolute()
        self.base = base
        self.root = base / 'maps' / 'temporary-checkouts'
        for path in (base, base / 'maps', self.root):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise CheckoutMapError('Temporary map root is unsafe', 403, 'checkout_map_storage')
            path.mkdir(exist_ok=True)
        self.index = self.root / 'index.json'
        self._restore()

    def _read_json(self, path, maximum=65536):
        """Read only ordinary bounded metadata, never follow a marker/index link."""
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            raise CheckoutMapError('Temporary map metadata is unsafe', 403, 'checkout_map_storage')
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise CheckoutMapError('Temporary map metadata is invalid', 503, 'checkout_map_storage') from None

    def _root_guard(self):
        """Recheck owned ancestors against replacement links before reads or deletion."""
        for path in (self.base, self.base / 'maps', self.root):
            if path.is_symlink() or not path.is_dir():
                raise CheckoutMapError('Temporary map root is unsafe', 403, 'checkout_map_storage')

    def _write_index(self):
        """Atomically persist only this manager's records in its private directory."""
        self._root_guard()
        if self.index.is_symlink():
            raise CheckoutMapError('Temporary map index is unsafe', 403, 'checkout_map_storage')
        # A never-used optional feature has no persistent session state. Status,
        # sweeps and shutdown must not create a late write in the canonical app.
        # Once an index exists, persist its empty list to retire the last session.
        if not self.entries and not self.index.exists():
            return
        temporary = self.root / ('index-'+uuid4().hex+'.tmp')
        records = []
        for entry in self.entries.values():
            record = dict(entry.record)
            record['state'] = entry.state
            record['cleanupError'] = entry.cleanup_error
            records.append(record)
        encoded = json.dumps({'schemaVersion': SCHEMA, 'sessions': records}, allow_nan=False)
        with temporary.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
        temporary.replace(self.index)

    def _checked_folder(self, record):
        """Resolve deletion exclusively from a validated opaque ID and exact marker."""
        self._root_guard()
        identifier = record.get('sessionId')
        if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
            raise CheckoutMapError('Invalid temporary map identity', 403, 'checkout_map_storage')
        path = self.root / identifier
        if path.is_symlink() or not path.is_dir():
            raise CheckoutMapError('Temporary map folder is unsafe', 403, 'checkout_map_storage')
        marker = self._read_json(path / MARKER, 1024)
        expected = {'schemaVersion': SCHEMA, 'sessionId': identifier, 'token': record.get('markerToken')}
        if marker != expected:
            raise CheckoutMapError('Temporary map ownership marker differs', 403, 'checkout_map_storage')
        return path

    def _new_store(self, path, runtime_python):
        """Create one private extraction/health owner with no canonical store dependency."""
        store = self.store_factory(path)
        store.graphify_runtime = _CapturedRuntime(path, runtime_python)
        try:
            health = CodeHealthService(store)
        except BaseException:
            store.close()
            raise
        return store, health

    def _restore(self):
        """Restore exact known marked folders; expire without opening a second writer."""
        if not self.index.exists() and not self.index.is_symlink():
            return
        value = self._read_json(self.index, 128 * 1024)
        if (not isinstance(value, dict) or value.get('schemaVersion') != SCHEMA
                or not isinstance(value.get('sessions'), list) or len(value['sessions']) > MAX_SESSIONS):
            raise CheckoutMapError('Temporary map index is invalid', 503, 'checkout_map_storage')
        try:
            for record in value['sessions']:
                if not isinstance(record, dict):
                    raise CheckoutMapError('Invalid temporary record', 503, 'checkout_map_storage')
                self._validate_record(record)
                identifier = record.get('sessionId')
                if (isinstance(identifier, str) and re.fullmatch('[a-f0-9]{32}', identifier)
                        and record.get('state') == 'deleting' and record.get('retiredReason') == 'capacity'):
                    path = self.root / identifier
                    if not path.exists() and not path.is_symlink():
                        # The previous process removed this folder before finalizing the index.
                        continue
                path = self._checked_folder(record)
                if identifier in self.entries:
                    raise CheckoutMapError('Duplicate temporary identity', 503, 'checkout_map_storage')
                remaining = min(TTL_SECONDS, record['expiresAtEpoch']-self.wall_clock())
                state = record.get('state', 'active')
                store, health = self._new_store(path, record.get('runtimePython'))
                entry = CheckoutEntry(identifier, path, record, self.clock()+max(0, remaining), store, health,
                                      state=state, cleanup_error=record.get('cleanupError'))
                self.entries[identifier] = entry
            self.sweep()
        except BaseException:
            for entry in self.entries.values():
                entry.store.close()
            raise

    def _validate_record(self, record):
        """Refuse tampered ownership/time/path metadata rather than widening cleanup."""
        _text(record.get('ownerKey'), 128, 'owner key')
        _text(record.get('label'), 200, 'label')
        if not isinstance(record.get('markerToken'), str) or not re.fullmatch('[a-f0-9]{32}', record['markerToken']):
            raise CheckoutMapError('Invalid recorded ownership token', 503, 'checkout_map_storage')
        checkout = record.get('checkout')
        if not isinstance(checkout, str) or not Path(checkout).is_absolute() or len(checkout) > 4096:
            raise CheckoutMapError('Invalid recorded checkout', 503, 'checkout_map_storage')
        expected = sha256(checkout.encode()).hexdigest()[:16]
        if record.get('projectId') != expected or record.get('state', 'active') not in {'active', 'deleting', 'cleanup-failed'}:
            raise CheckoutMapError('Invalid recorded project', 503, 'checkout_map_storage')
        for name in ('createdAtEpoch', 'expiresAtEpoch'):
            value = record.get(name)
            if type(value) not in (float, int) or (type(value) is float and not math.isfinite(value)):
                raise CheckoutMapError('Invalid recorded expiry', 503, 'checkout_map_storage')
            # Finite JSON may still lie outside datetime's UTC range. Validate
            # using the same conversion as metadata, before opening/restoring a
            # store or treating a malformed negative expiry as safe to collect.
            _utc(value)
        runtime = record.get('runtimePython')
        if runtime is not None and (not isinstance(runtime, str) or not Path(runtime).is_absolute()):
            raise CheckoutMapError('Invalid recorded runtime', 503, 'checkout_map_storage')

    def _metadata(self, entry):
        """Return visible lifetime/build facts without touching the inactivity deadline."""
        projects = entry.store.list_projects()
        project = next((item for item in projects.get('projects', []) if item.get('id') == entry.project_id), None)
        return {'sessionId': entry.session_id, 'projectId': entry.project_id, 'temporary': True,
                'path': str(entry.path), 'checkout': entry.record['checkout'], 'ownerKey': entry.record['ownerKey'],
                'label': entry.record['label'], 'state': entry.state, 'ttlSeconds': TTL_SECONDS,
                'createdAt': _utc(entry.record['createdAtEpoch']), 'expiresAt': _utc(entry.record['expiresAtEpoch']),
                'project': project, 'activeJob': projects.get('activeJob'), 'activeLeases': entry.leases,
                'cleanupError': entry.cleanup_error,
                'reclaim': self._reclaim_status(entry, self.clock()),
                'runtimeSelection': {'python': entry.record.get('runtimePython'),
                    'provenance': 'captured-host-selection; private store only',
                    'environmentOverrideApplies': bool(os.environ.get('ATLAS_GRAPHIFY_PYTHON'))}}

    def _reclaim_status(self, entry, now):
        """Describe inactivity since creation or the last successful useful read."""
        idle = max(0, int(TTL_SECONDS - (entry.deadline - now)))
        eligible = (
            entry.state == 'active' and RECLAIM_IDLE_SECONDS <= idle < TTL_SECONDS
            and not entry.leases and entry.store.active_job is None
        )
        return {'idleSeconds': idle, 'minIdleSeconds': RECLAIM_IDLE_SECONDS, 'eligible': eligible}

    def _reclaim_idle_slot(self):
        """Under the admission lock, remove at most the longest-idle eligible map."""
        now = self.clock()
        candidates = sorted(
            (entry for entry in self.entries.values() if self._reclaim_status(entry, now)['eligible']),
            key=lambda entry: entry.deadline,
        )
        for entry in candidates:
            try:
                path = self._deletion_path(entry)
            except (CheckoutMapError, OSError):
                continue
            idle_seconds = self._reclaim_status(entry, now)['idleSeconds']
            entry.state = 'deleting'
            entry.record['retiredReason'] = 'capacity'
            try:
                self._write_index()
            except BaseException:
                entry.state = 'active'
                entry.record.pop('retiredReason')
                raise
            self._remove_folder(entry, path)
            self._write_index()
            if entry.session_id not in self.entries:
                return {'sessionId': entry.session_id, 'idleSeconds': idle_seconds}
            break
        return None

    def _retire_expired(self):
        """Monotonic expiry retires admissions even while a builder/read remains alive."""
        for entry in self.entries.values():
            if entry.state == 'active' and self.clock() >= entry.deadline:
                entry.state = 'deleting'
                entry.record['retiredReason'] = 'expired'

    def _deletion_path(self, entry):
        """Verify the session marker and reject links anywhere in its private folder."""
        path = self._checked_folder(entry.record)
        for directory, folders, files in os.walk(path, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in folders + files):
                raise CheckoutMapError('Temporary folder contains a link', 403, 'checkout_map_storage')
        return path

    def _remove_folder(self, entry, path):
        """Close the drained store and remove the previously verified folder."""
        try:
            entry.store.close()
            shutil.rmtree(path)
            self.entries.pop(entry.session_id)
        except Exception:
            entry.state, entry.cleanup_error = 'cleanup-failed', 'Owned map cleanup failed; folder retained for retry'

    def _collect(self):
        """Delete only drained known folders; cleanup errors remain visible for retry."""
        for entry in list(self.entries.values()):
            if entry.state == 'active' or entry.leases or entry.store.active_job is not None:
                continue
            try:
                path = self._deletion_path(entry)
            except Exception:
                entry.state, entry.cleanup_error = 'cleanup-failed', 'Owned map cleanup failed; folder retained for retry'
                continue
            self._remove_folder(entry, path)

    def sweep(self):
        """Expire admissions and collect drained folders; polling does not keep maps alive."""
        with self.lock:
            self._retire_expired()
            self._collect()
            self._write_index()

    def _entry(self, session_id):
        """Look up only a live server-managed identity; never form a caller path."""
        self._retire_expired()
        entry = self.entries.get(session_id) if isinstance(session_id, str) else None
        if self.closed or entry is None or entry.state != 'active':
            raise CheckoutMapError('Temporary map expired, retired or unavailable', 410, 'checkout_map_gone')
        self._checked_folder(entry.record)
        return entry

    def ensure(self, checkout, owner_key, label=None, *, reclaim_idle=False):
        """Start or reuse this task's map, optionally reclaiming one idle slot at capacity."""
        owner_key = _text(owner_key, 128, 'owner key')
        source = Path(checkout).expanduser() if isinstance(checkout, (str, Path)) else Path('.')
        if not source.is_absolute() or not source.is_dir():
            raise CheckoutMapError('Choose an existing absolute checkout directory')
        source = source.resolve()
        if source == self.base or self.base in source.parents or source in self.root.parents:
            raise CheckoutMapError('Checkout must be separate from temporary storage', 403)
        label = _text(label or source.name, 200, 'label')
        with self.lock:
            if self.closed:
                raise CheckoutMapError('Temporary maps are shutting down', 503)
            self._root_guard()
            self.sweep()
            for entry in self.entries.values():
                if entry.state == 'active' and entry.record['ownerKey'] == owner_key and entry.record['checkout'] == str(source):
                    self._checked_folder(entry.record)
                    return self._metadata(entry)
            reclaimed = None
            if len(self.entries) >= self.capacity and reclaim_idle:
                reclaimed = self._reclaim_idle_slot()
            if len(self.entries) >= self.capacity:
                recovery = (
                    'No idle slot could be reclaimed.' if reclaim_idle else
                    'Use ensure_checkout_map_with_reclaim to replace one eligible idle map.'
                )
                raise CheckoutMapError(
                    f'Temporary checkout map limit reached ({self.capacity}). {recovery} '
                    'Use list_checkout_maps to inspect inactivity and expiry.',
                    429, 'checkout_map_capacity',
                )
            identifier, token = uuid4().hex, uuid4().hex
            path = self.root / identifier
            path.mkdir()
            (path / MARKER).write_text(json.dumps({'schemaVersion': SCHEMA, 'sessionId': identifier, 'token': token}))
            now = self.wall_clock()
            record = {'sessionId': identifier, 'markerToken': token, 'ownerKey': owner_key,
                      'checkout': str(source), 'label': label, 'projectId': sha256(str(source).encode()).hexdigest()[:16],
                      'createdAtEpoch': now, 'expiresAtEpoch': now+TTL_SECONDS, 'runtimePython': self.runtime_python}
            store = None
            try:
                store, health = self._new_store(path, self.runtime_python)
                entry = CheckoutEntry(identifier, path, record, self.clock()+TTL_SECONDS, store, health)
                self.entries[identifier] = entry
                self._write_index()
                store.add(str(source))
                metadata = self._metadata(entry)
                if reclaimed is not None:
                    metadata['reclaimed'] = reclaimed
                return metadata
            except BaseException:
                if store is not None:
                    # Failed add may already have queued a builder. Retire its
                    # private folder; never delete underneath background work.
                    if identifier in self.entries:
                        self.entries[identifier].state = 'deleting'
                        self._collect()
                        self._write_index()
                    else:
                        store.close()
                else:
                    # No store or builder was created, so only these newly
                    # allocated marker bytes can exist in the owned folder.
                    shutil.rmtree(path)
                raise

    def status(self, session_id):
        """Read status including pending cleanup; never extend TTL or revive IDs."""
        with self.lock:
            self.sweep()
            entry = self.entries.get(session_id)
            if entry is None:
                raise CheckoutMapError('Temporary map is unavailable', 410, 'checkout_map_gone')
            return self._metadata(entry)

    def list(self):
        """List only private session metadata and pending deletions, without keepalive."""
        with self.lock:
            self.sweep()
            return {'sessions': [self._metadata(entry) for entry in self.entries.values()]}

    @contextmanager
    def access(self, session_id):
        """Pin one useful read; renew only successful work still live at final recheck."""
        with self.lock:
            entry = self._entry(session_id)
            entry.leases += 1
        try:
            yield entry
            with self.lock:
                self._entry(session_id)
                old_deadline, old_expiry = entry.deadline, entry.record['expiresAtEpoch']
                entry.deadline = self.clock()+TTL_SECONDS
                entry.record['expiresAtEpoch'] = self.wall_clock()+TTL_SECONDS
                try:
                    self._write_index()
                except BaseException:
                    entry.deadline, entry.record['expiresAtEpoch'] = old_deadline, old_expiry
                    raise
        finally:
            with self.lock:
                entry.leases -= 1
                self._retire_expired(); self._collect(); self._write_index()

    def rebuild(self, session_id):
        """Explicitly rebuild only the selected private store without a TTL keepalive."""
        with self.lock:
            entry = self._entry(session_id)
            result = entry.store.rebuild(entry.project_id)
            metadata = self._metadata(entry)
            metadata['job'] = result.get('job')
            return metadata

    def finish(self, session_id):
        """Retire immediately; expose pending drain rather than promise instant cancellation."""
        with self.lock:
            entry = self.entries.get(session_id)
            if entry is None:
                raise CheckoutMapError('Temporary map is unavailable', 410, 'checkout_map_gone')
            entry.state = 'deleting'
            entry.record['retiredReason'] = 'finished'
            self._collect(); self._write_index()
            result = self._metadata(entry)
            if session_id not in self.entries:
                result['state'] = 'deleted'
            return result

    def start(self):
        """Start one expiry sweeper; all entry reads also enforce deadlines directly."""
        with self.lock:
            if self.thread is None and not self.closed:
                self.thread = Thread(target=self._run, name='atlas-checkout-expiry', daemon=True)
                self.thread.start()

    def _run(self):
        """Retry owned cleanup every five seconds; failures remain inspectable metadata."""
        while not self.stop.wait(5):
            try:
                self.sweep()
            except (CheckoutMapError, OSError):
                # Do not widen filesystem ownership to repair a bad external link.
                pass

    def close(self):
        """Drain stores, retain valid maps/index for restart, collect only retired maps."""
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.stop.set()
            entries = list(self.entries.values())
        if self.thread is not None:
            self.thread.join(timeout=2)
        failure = None
        for entry in entries:
            try:
                entry.store.close()
            except Exception as error:
                if failure is None:
                    failure = error
        with self.lock:
            self._retire_expired(); self._collect(); self._write_index()
        if failure is not None:
            raise failure
