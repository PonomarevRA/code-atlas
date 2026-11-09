"""Active-use discovery and a bounded queue for optional, already installed engines.

No watcher, installer, build, restore or source-project callback lives here. Only
trusted host adapters enter the registry. Map sidecars and graph publication are
owned by the semantic service; executable paths stay in this host-local cache.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from .usage_metrics import observe
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
from threading import Event, RLock, Timer
import time
from typing import Protocol
from uuid import uuid4

from .process_lock import ProcessLock
from .semantic_environment import discovery_environment_fingerprint
from .semantic_models import (SemanticAvailability, SemanticContext, SemanticResult,
                              semantic_availability_from_dict)

DISCOVERY_TTL = 24 * 60 * 60
MAX_CACHE_RECORDS = 512
MAX_CACHE_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_RETAINED_RESULT_BYTES = 16 * 1024 * 1024
PROBE_LOCK_SHARDS = 4096


@dataclass(frozen=True)
class ProbeOutcome:
    """Portable availability plus a private, bounded local engine asset inventory."""
    availability: SemanticAvailability
    assets: tuple[Path, ...] = ()

    def __post_init__(self):
        """Adapters identify files, never recursively watched installation directories."""
        if not isinstance(self.availability, SemanticAvailability) or len(self.assets) > 16:
            raise ValueError('Invalid semantic probe outcome')
        paths = tuple(Path(path) for path in self.assets)
        if any(not path.is_absolute() for path in paths):
            raise ValueError('Semantic engine asset paths must be absolute')
        object.__setattr__(self, 'assets', paths)


class SemanticProvider(Protocol):
    """Trusted callbacks must honor cancellation and use bounded process transports."""
    provider_id: str

    def probe(self, context: SemanticContext, cancel: Event) -> ProbeOutcome:
        """Check compatibility and local context without indexing or installation."""
        ...

    def analyze(self, context: SemanticContext, probe: ProbeOutcome,
                request: dict, cancel: Event) -> SemanticResult:
        """Return static evidence for pinned captured sources; never execute the project."""
        ...


def _digest(value):
    """Use stable JSON identities for environment and captured-request deduplication."""
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return sha256(payload).hexdigest()


def _asset_stamps(paths):
    """Cheap before-launch revalidation; adapters fingerprint their actual engine closure."""
    stamps = []
    for path in paths:
        stat = path.stat()
        if not path.is_file():
            raise ValueError('Semantic engine asset is not a file')
        stamps.append([str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino])
    return stamps


class SemanticRuntime:
    """Share discovery across snapshots, while keeping analysis results source-specific."""

    def __init__(self, cache_dir, registry, *, clock=time.time, max_pending=32, usage=None):
        """Construct lightweight handles; no engine discovery or process starts here."""
        if not 1 <= max_pending <= 64 or len(registry) > 128:
            raise ValueError('Invalid semantic runtime budget')
        self.usage = usage
        self.directory = Path(cache_dir)
        self.registry = dict(registry)
        self.clock, self.max_pending = clock, max_pending
        self.host = _digest([platform.node(), platform.system(), platform.machine()])
        self.lock = RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='atlas-semantic')
        self.records, self.results = OrderedDict(), OrderedDict()
        self.result_sizes = {}
        self.result_assets = {}
        self.jobs = {}
        self.closed = False

    def _environment_key(self, context, provider_id):
        """Share discovery across source revisions, but never across host engine choices."""
        return _digest([self.host, context.workspace_id, os.path.abspath(context.workspace_root),
                        context.target_id, provider_id, context.config_fingerprint,
                        context.provider_fingerprint, discovery_environment_fingerprint()])

    @staticmethod
    def _result_key(context, provider_id):
        """A result cannot be reused across captured source or provider revisions."""
        return _digest([asdict(context), provider_id])

    def _path(self, key):
        """Cache filenames contain hashes only; local workspace names stay in memory."""
        return self.directory / f'{key}.json'

    def _read(self, key, *, refresh=False):
        """Read one bounded cache record; cache hits never scan or stat engine assets."""
        if key in self.records and not refresh:
            return self.records[key]
        record = {'availability': SemanticAvailability('unchecked'), 'assets': []}
        try:
            path = self._path(key)
            with path.open('rb') as handle:
                encoded = handle.read(MAX_CACHE_BYTES + 1)
            if len(encoded) > MAX_CACHE_BYTES:
                raise ValueError('Oversized discovery cache')
            payload = json.loads(encoded)
            if payload['schema'] != 1 or payload['key'] != key:
                raise ValueError('Unsupported discovery cache')
            availability = semantic_availability_from_dict(payload['availability'])
            assets = payload['assets']
            if not isinstance(assets, list) or len(assets) > 16:
                raise ValueError('Invalid engine asset inventory')
            for asset in assets:
                if (not isinstance(asset, list) or len(asset) != 4
                        or not isinstance(asset[0], str) or not Path(asset[0]).is_absolute()
                        or any(type(value) is not int or value < 0 for value in asset[1:])):
                    raise ValueError('Invalid engine asset stamp')
            record = {'availability': availability, 'assets': assets}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.records[key] = record
        self.records.move_to_end(key)
        while len(self.records) > MAX_CACHE_RECORDS:
            self.records.popitem(last=False)
        return record

    def _write(self, key, record):
        """Atomic local persistence failure is contained; the baseline map remains usable."""
        self.records[key] = record
        self.records.move_to_end(key)
        while len(self.records) > MAX_CACHE_RECORDS:
            self.records.popitem(last=False)
        temporary = self._path(key).with_suffix(f'.{uuid4().hex}.tmp')
        payload = {'schema': 1, 'key': key, 'availability': asdict(record['availability']),
                   'assets': record['assets']}
        try:
            encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            if len(encoded) > MAX_CACHE_BYTES:
                raise ValueError('Oversized discovery cache')
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(encoded)
            temporary.replace(self._path(key))
        except (OSError, ValueError):
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def status(self, context, provider_ids):
        """Status polling only reads captured availability; it never schedules work."""
        with self.lock:
            statuses = {}
            for provider_id in provider_ids:
                key = self._environment_key(context, provider_id)
                record = self._read(key)
                if record['availability'].status == 'checking' and ('probe', key) not in self.jobs:
                    record = self._read(key, refresh=True)
                statuses[provider_id] = record['availability']
            return statuses

    def on_use(self, context, provider_ids, force=False):
        """On actual map/node use, queue due positive/negative checks and return immediately."""
        provider_ids = tuple(provider_ids)
        with self.lock:
            for provider_id in provider_ids:
                if self.closed or len(self.jobs) >= self.max_pending:
                    break
                key = self._environment_key(context, provider_id)
                if ('probe', key) in self.jobs:
                    continue
                record = self._read(key)
                availability, now = record['availability'], self.clock()
                rollback = availability.checked_at is not None and now < availability.checked_at
                pending = availability.status == 'checking'
                if not force and not rollback and not pending and availability.next_check_at is not None:
                    if now < availability.next_check_at:
                        if self.usage:
                            observe(self.usage, 'cache_hit', provider_id)
                        continue
                # Stable lock inodes cannot be deleted safely while another
                # process may hold them. A fixed shard set bounds their lifetime
                # count; a rare collision simply leaves this check due next use.
                shard = int(key[:8], 16) % PROBE_LOCK_SHARDS
                lease = ProcessLock(self.directory / f'probe-{shard:03x}.lock')
                try:
                    lease.__enter__()
                except (OSError, RuntimeError):
                    continue
                # Re-read after acquiring the cross-process lease. Another map may
                # have completed the exact check while this instance had cached data.
                record = self._read(key, refresh=True)
                availability = record['availability']
                if (not force and availability.checked_at is not None
                        and now >= availability.checked_at and availability.next_check_at is not None
                        and now < availability.next_check_at):
                    if availability.status == 'checking':
                        record['availability'] = replace(availability, status='failed', reason='interrupted-probe')
                        self._write(key, record)
                    lease.__exit__(None, None, None)
                    continue
                record = {'availability': SemanticAvailability('checking', checked_at=now,
                          next_check_at=now + DISCOVERY_TTL), 'assets': []}
                self._write(key, record)
                cancel = Event()
                future = self.executor.submit(self._probe, key, provider_id, deepcopy(context), cancel,
                                              lease, record['availability'])
                self.jobs[('probe', key)] = (future, cancel)
            return self.status(context, provider_ids)

    def _probe(self, key, provider_id, context, cancel, lease, attempt):
        """Publish one attempt with a fixed rolling TTL, including failures and cancellation."""
        timer = Timer(10, cancel.set)
        timer.daemon = True
        timer.start()
        measurement = None
        availability = SemanticAvailability('failed', 'probe-interrupted')
        try:
            provider = self.registry.get(provider_id)
            if provider is not None and self.usage:
                measurement = observe(self.usage, 'begin', provider_id, 'semantic_probe')
            outcome = provider.probe(context, cancel) if provider else ProbeOutcome(
                SemanticAvailability('incompatible', 'host-strategy-unavailable'))
            if not isinstance(outcome, ProbeOutcome):
                raise ValueError('Invalid provider probe result')
            availability = outcome.availability
            if availability.status not in {'available', 'missing', 'incompatible', 'failed'}:
                raise ValueError('Probe did not return a terminal availability state')
            if cancel.is_set():
                availability = SemanticAvailability('failed', 'cancelled-probe')
            if availability.status == 'available' and (not availability.engine_fingerprint or not outcome.assets):
                raise ValueError('Available engine lacks identity')
            assets = _asset_stamps(outcome.assets) if availability.status == 'available' else []
        except Exception as error:
            availability = SemanticAvailability('failed', f'probe-failed:{type(error).__name__}')
            assets = []
        finally:
            timer.cancel()
            if measurement:
                outcome_name = 'error'
                if cancel.is_set():
                    outcome_name = 'cancelled'
                elif availability.status == 'available':
                    outcome_name = 'success'
                elif availability.status in {'missing', 'incompatible'}:
                    outcome_name = 'unavailable'
                observe(measurement, 'finish', outcome_name)
        try:
            with self.lock:
                if cancel.is_set() or self.closed:
                    availability = SemanticAvailability('failed', 'cancelled-probe')
                    assets = []
                self._write(key, {'availability': replace(availability, checked_at=attempt.checked_at,
                            next_check_at=attempt.next_check_at), 'assets': assets})
                self.jobs.pop(('probe', key), None)
            self._trim_disk()
        finally:
            lease.__exit__(None, None, None)

    def _trim_disk(self):
        """Bound persisted discoveries after background writes, never on a map cache hit."""
        try:
            files = sorted(self.directory.glob('*.json'), key=lambda path: path.stat().st_mtime_ns)
            for path in files[:-MAX_CACHE_RECORDS]:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def submit_analysis(self, context, provider_id, request):
        """Queue captured-source analysis only after discovery and fresh asset validation."""
        encoded = json.dumps(request, sort_keys=True, ensure_ascii=False).encode('utf-8')
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError('Semantic analysis request exceeds its budget')
        result_key = self._result_key(context, provider_id)
        job_key = ('analysis', result_key, sha256(encoded).hexdigest())
        with self.lock:
            if job_key in self.jobs:
                return self.jobs[job_key][0]
            if self.closed or len(self.jobs) >= self.max_pending or provider_id not in self.registry:
                return None
            environment_key = self._environment_key(context, provider_id)
            record = self._read(environment_key)
            if record['availability'].status != 'available':
                return None
            cancel = Event()
            future = self.executor.submit(self._analyze, job_key, result_key, environment_key,
                                          deepcopy(context), provider_id, deepcopy(request), cancel)
            self.jobs[job_key] = (future, cancel)
            return future

    def _analyze(self, job_key, result_key, environment_key, context, provider_id, request, cancel):
        """Reject changed engines and mismatched result identities before retaining any facts."""
        timer = Timer(60, cancel.set)
        timer.daemon = True
        timer.start()
        measurement = None
        measured_outcome = 'error'
        measured_facts = ()
        try:
            with self.lock:
                record = deepcopy(self._read(environment_key))
            availability = record['availability']
            paths = tuple(Path(asset[0]) for asset in record['assets'])
            if _asset_stamps(paths) != record['assets']:
                raise ValueError('engine-changed')
            if cancel.is_set():
                return None
            outcome = ProbeOutcome(availability, paths)
            if self.usage:
                measurement = observe(self.usage, 'begin', provider_id, 'semantic_analyze')
            result = self.registry[provider_id].analyze(context, outcome, request, cancel)
            if (not isinstance(result, SemanticResult) or result.context != context
                    or result.provider_id != provider_id
                    or result.engine_fingerprint != availability.engine_fingerprint):
                raise ValueError('Mismatched semantic result identity')
            result_size = len(json.dumps(result.to_dict()).encode('utf-8'))
            if result_size > MAX_RESULT_BYTES:
                raise ValueError('Oversized semantic result')
            if cancel.is_set() or _asset_stamps(paths) != record['assets']:
                return None
            with self.lock:
                # Off/close and publication share this lock. An engine may have
                # returned before cancellation while final asset validation ran.
                if cancel.is_set() or self.closed:
                    return None
                self.results[result_key] = result
                self.result_sizes[result_key] = result_size
                self.result_assets[result_key] = deepcopy(record)
                self.results.move_to_end(result_key)
                while len(self.results) > 16 or sum(self.result_sizes.values()) > MAX_RETAINED_RESULT_BYTES:
                    discarded, _ = self.results.popitem(last=False)
                    self.result_sizes.pop(discarded, None)
                    self.result_assets.pop(discarded, None)
            # Partial is a completed callback, not a claim of complete static coverage.
            measured_outcome = {'failed': 'error', 'cancelled': 'cancelled',
                                'unsupported': 'unavailable'}.get(result.status, 'success')
            measured_facts = result.facts
            return result
        except Exception as error:
            with self.lock:
                previous = self._read(environment_key)
                self._write(environment_key, {'availability': replace(previous['availability'],
                            status='failed', reason=f'analysis-failed:{type(error).__name__}'), 'assets': []})
            return None
        finally:
            timer.cancel()
            if measurement:
                observe(measurement, 'finish', 'cancelled' if cancel.is_set() else measured_outcome, measured_facts)
            with self.lock:
                self.jobs.pop(job_key, None)

    def result_for(self, context, provider_ids):
        """Return only results anchored to this exact snapshot/source/config/provider context."""
        with self.lock:
            return {provider_id: deepcopy(self.results[key]) for provider_id in provider_ids
                    if (key := self._result_key(context, provider_id)) in self.results}

    def result_is_current(self, result):
        """Revalidate bounded retained engine provenance immediately before external publication.

        This performs at most sixteen local file stats and never launches engines,
        refreshes discovery or changes historical results. Cancel revokes current
        publication eligibility while preserving result_for's historical evidence.
        """
        if not isinstance(result, SemanticResult):
            return False
        with self.lock:
            key = self._result_key(result.context, result.provider_id)
            expected = self.result_assets.get(key)
            if (self.closed or expected is None or self.results.get(key) != result
                    or result.provider_id not in self.registry):
                return False
            current = self._read(self._environment_key(result.context, result.provider_id))
            if (current['availability'].status != 'available'
                    or current['availability'].engine_fingerprint != result.engine_fingerprint
                    or current['assets'] != expected['assets']):
                return False
            try:
                return _asset_stamps(tuple(Path(asset[0]) for asset in expected['assets'])) == expected['assets']
            except (OSError, ValueError):
                return False

    def cancel(self, context, provider_ids):
        """Stop owned work on Off or context replacement; no historical facts are deleted."""
        with self.lock:
            for provider_id in provider_ids:
                environment_key = self._environment_key(context, provider_id)
                result_key = self._result_key(context, provider_id)
                self.result_assets.pop(result_key, None)
                for job_key, (_, cancellation) in self.jobs.items():
                    if job_key[:2] in {('probe', environment_key), ('analysis', result_key)}:
                        cancellation.set()

    def wait_for_idle(self, timeout=10):
        """A bounded test/shutdown barrier, never called from ordinary map reads."""
        with self.lock:
            futures = [job[0] for job in self.jobs.values()]
        return not wait(futures, timeout=timeout).not_done if futures else True

    def close(self):
        """Signal cooperative cancellation and stop accepting work without unbounded shutdown."""
        with self.lock:
            self.closed = True
            for _, cancellation in self.jobs.values():
                cancellation.set()
        self.wait_for_idle(timeout=3)
        self.executor.shutdown(wait=False)
