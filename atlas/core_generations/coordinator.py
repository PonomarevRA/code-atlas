"""Own bounded projection worker processes and atomic generation dispatch pins."""

from hashlib import sha256
import math
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Condition, Lock, RLock
import time
from uuid import uuid4

from .artifacts import GenerationError, copy_verified
from .protocol import MAX_FRAME_BYTES, decode_payload, encode_frame


def _duration(value, *, maximum=120):
    """Reject unbounded, zero and nonnumeric waits at the lifecycle boundary."""
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= maximum:
        raise GenerationError('generation_invalid', 'Timeout must be finite and bounded', 400)
    return value


class _Worker:
    """Own one child, one copied bundle and at most one in-flight pipe exchange."""

    def __init__(self, artifact):
        self.staging = TemporaryDirectory(prefix='atlas-module-generation-')
        self.process = None
        self._stop_lock = Lock()
        self.busy, self.retiring, self.dead = False, False, False
        try:
            copied, self.manifest = copy_verified(artifact, self.staging.name)
            self.bundle_hash = copied.bundle_hash
            bootstrap = Path(__file__).with_name('worker.py')
            self.bootstrap_hash = sha256(bootstrap.read_bytes()).hexdigest()
            self.process = subprocess.Popen([sys.executable, '-I', '-B', str(bootstrap),
                                             self.staging.name, copied.bundle_hash],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, cwd=self.staging.name)
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
        except BaseException:
            self.stop()
            raise

    def stop(self):
        """Reap only this owned process, escalate bounded terminate to kill, close pipes."""
        with self._stop_lock:
            self._stop_owned()

    def _stop_owned(self):
        """Serialize cancellation, forced drain and close for the same owned child."""
        self.dead = True
        process = self.process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        self.staging.cleanup()

    def _check(self, deadline, cancel_event):
        """Cooperative parent wait can terminate a whole owned compute generation."""
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationError('worker_cancelled', 'Module computation cancelled', 409)
        if time.monotonic() >= deadline:
            raise GenerationError('worker_timeout', 'Module computation deadline expired', 504)
        if self.dead or self.process.poll() is not None:
            raise GenerationError('worker_unavailable', 'Compute generation exited')

    def _read(self, count, deadline, cancel_event):
        """Collect exactly a bounded count while checking cancellation and child death."""
        output = bytearray()
        while len(output) < count:
            self._check(deadline, cancel_event)
            try:
                ready, _, _ = select.select([self.process.stdout], [], [], min(0.05, max(0, deadline-time.monotonic())))
                if ready:
                    chunk = os.read(self.process.stdout.fileno(), min(65536, count-len(output)))
                    if not chunk:
                        raise GenerationError('worker_unavailable', 'Compute generation closed its pipe')
                    output.extend(chunk)
            except (OSError, ValueError):
                raise GenerationError('worker_unavailable', 'Compute generation pipe closed') from None
        return bytes(output)

    def receive(self, deadline, cancel_event=None):
        """Decode one response after validating its declared allocation size."""
        size = struct.unpack('!I', self._read(4, deadline, cancel_event))[0]
        if size > MAX_FRAME_BYTES:
            raise GenerationError('worker_budget', 'Worker response exceeds byte budget', 413)
        return decode_payload(self._read(size, deadline, cancel_event))

    def exchange(self, request, deadline, cancel_event=None):
        """Send once, read once; partial writes/timeouts are never replayed."""
        data = encode_frame(request)
        offset = 0
        while offset < len(data):
            self._check(deadline, cancel_event)
            try:
                _, ready, _ = select.select([], [self.process.stdin], [], 0.05)
                if ready:
                    offset += os.write(self.process.stdin.fileno(), data[offset:offset+65536])
            except (OSError, ValueError):
                raise GenerationError('worker_unavailable', 'Compute generation pipe closed') from None
        return self.receive(deadline, cancel_event)

    def verify_identity(self, value):
        """Require identity measured by the actual imported worker source/process."""
        expected = {'generationId': self.manifest['generationId'], 'bundleHash': self.bundle_hash,
                    'computeHash': self.manifest['files']['atlas/module_compute.py'],
                    'pid': self.process.pid, 'protocol': self.manifest['protocol'],
                    'semantics': self.manifest['semantics'], 'storage': self.manifest['storage'],
                    'runtime': self.manifest['runtime'], 'bootstrapHash': self.bootstrap_hash}
        if value != expected:
            raise GenerationError('worker_identity', 'Compute generation identity mismatch', 409)
        return expected


class CoreGenerations:
    """Single-host supervisor; workers never own store, jobs, cursors or transports.

    Explicit trusted deployment calls prepare/activate. Normal application startup
    remains inline unless composition opts in. Maximum two retained generations
    and one in-flight operation per generation prevent hidden queues/process growth.
    """

    def __init__(self):
        self._condition = Condition(RLock())
        self._workers = {}
        self._active = None
        self._closed = False
        self._preparing = False
        self._starting = None

    def prepare(self, artifact, *, timeout=10):
        """Start and canary a copied bundle; failures never change current dispatch."""
        _duration(timeout)
        with self._condition:
            if self._closed or self._preparing or len(self._workers) >= 2:
                raise GenerationError('generation_limit', 'Generation admission is unavailable', 429)
            self._preparing = True
        worker = None
        try:
            # Close cannot miss a subprocess between Popen and registration.
            with self._condition:
                if self._closed:
                    raise GenerationError('generation_closed', 'Supervisor is closed')
                worker = _Worker(artifact)
                self._starting = worker
            deadline = time.monotonic() + timeout
            ready = worker.receive(deadline)
            if ready.get('ready') is not True:
                raise GenerationError('worker_unready', 'Worker did not become ready')
            identity = worker.verify_identity(ready.get('identity'))
            canary = {'units': [{'id': 'u', 'sourceIds': ['n']}], 'edges': [],
                      'coverage': {'status': 'complete'}, 'projectionHash': 'readiness-canary',
                      'snapshotId': 'readiness', 'counts': {'externalDependencies': 0}}
            response = worker.exchange({'operation': 'module-projection', 'requestId': 'canary',
                                        'projection': canary, 'algorithms': ['current'],
                                        'seed': 42, 'resolution': 1.0}, deadline)
            result = self._result(worker, response, 'canary')
            assessment, comparison = result['assessment'], result['comparison']
            if (not isinstance(assessment, dict) or not isinstance(comparison, dict)
                    or assessment.get('projectionHash') != 'readiness-canary'
                    or assessment.get('metrics', {}).get('groupCount') != 1
                    or comparison.get('projectionHash') != 'readiness-canary'
                    or not comparison.get('proposals')
                    or comparison['proposals'][0].get('status') != 'available'):
                raise GenerationError('worker_unready', 'Actual computation canary failed')
            generation = identity['generationId']
            with self._condition:
                if self._closed or generation in self._workers:
                    raise GenerationError('generation_invalid', 'Generation is closed or already exists', 409)
                self._workers[generation] = worker
            return dict(identity)
        except BaseException:
            if worker is not None:
                worker.stop()
            raise
        finally:
            with self._condition:
                self._preparing = False
                self._starting = None
                self._condition.notify_all()

    def activate(self, generation_id):
        """Atomically pin future dispatch to a prepared live process; no auto fallback."""
        with self._condition:
            worker = self._workers.get(generation_id)
            if self._closed or worker is None or worker.retiring or worker.dead or worker.process.poll() is not None:
                raise GenerationError('generation_unready', 'Generation cannot be activated', 409)
            previous = self._active
            self._active = generation_id
            return {'previous': previous, 'current': generation_id}

    def _result(self, worker, response, request_id):
        """Reject mismatched replies and preserve declared domain errors without retry."""
        worker.verify_identity(response.get('identity'))
        if response.get('requestId') != request_id:
            raise GenerationError('worker_protocol', 'Worker request identity mismatch')
        error = response.get('error')
        if isinstance(error, dict):
            code, status = error.get('code'), error.get('status')
            if not isinstance(code, str) or len(code) > 100 or type(status) is not int or not 400 <= status <= 599:
                raise GenerationError('worker_protocol', 'Invalid worker error')
            raise GenerationError(code, 'Module computation failed', status)
        result = response.get('result')
        if not isinstance(result, dict) or set(result) != {'assessment', 'comparison'}:
            raise GenerationError('worker_protocol', 'Invalid computation result')
        return result

    def compute(self, projection, *, algorithms=('current', 'louvain', 'leiden'), seed=42,
                resolution=1.0, timeout=30, cancel_event=None):
        """Pin one generation and compute once on serialized captured JSON facts.

        A timeout/cancellation or corrupt pipe retires the affected process. No
        operation migrates to N+1 and no result is silently replayed after failure.
        """
        _duration(timeout)
        # Capture caller data before admission; the child receives independent JSON.
        request_id = uuid4().hex
        request = {'operation': 'module-projection', 'requestId': request_id,
                   'projection': projection, 'algorithms': algorithms, 'seed': seed, 'resolution': resolution}
        data = encode_frame(request)
        request = decode_payload(data[4:])
        with self._condition:
            worker = self._workers.get(self._active)
            if self._closed or worker is None or worker.dead or worker.retiring:
                raise GenerationError('generation_unready', 'No active compute generation', 503)
            if worker.busy:
                raise GenerationError('module_analysis_busy', 'Compute generation is busy', 429)
            worker.busy = True
        try:
            response = worker.exchange(request, time.monotonic()+timeout, cancel_event)
            return self._result(worker, response, request_id)
        except GenerationError as error:
            if error.code.startswith('worker_'):
                worker.stop()
            raise
        finally:
            with self._condition:
                worker.busy = False
                self._condition.notify_all()

    def retire(self, generation_id, *, deadline=None):
        """Drain an inactive generation until an absolute monotonic deadline, then stop.

        The default drain is one second; caller deadlines must be within120seconds.
        Killing outstanding read-only work reports an error to its pinned caller.
        """
        now = time.monotonic()
        if deadline is None:
            deadline = now + 1
        if type(deadline) not in (int, float):
            raise GenerationError('generation_invalid', 'Deadline must be monotonic seconds', 400)
        _duration(deadline-now)
        with self._condition:
            worker = self._workers.get(generation_id)
            if generation_id == self._active:
                raise GenerationError('generation_active', 'Activate a successor before retirement', 409)
            if worker is None:
                raise GenerationError('generation_unknown', 'Unknown compute generation', 404)
            worker.retiring = True
            while worker.busy and time.monotonic() < deadline:
                self._condition.wait(timeout=min(0.05, max(0, deadline-time.monotonic())))
            forced = worker.busy
            # Retiring processes still consume admission and remain visible to
            # close until wait/kill has actually reaped them. Dropping this pin
            # first would permit prepare to spawn a third live process.
        worker.stop()
        with self._condition:
            if self._workers.get(generation_id) is worker:
                self._workers.pop(generation_id)
            self._condition.notify_all()
        return {'generationId': generation_id, 'forced': forced, 'pid': worker.process.pid}

    def state(self):
        """Return bounded supervisor diagnostics, never a new network/admin surface."""
        with self._condition:
            return {'active': self._active, 'closed': self._closed,
                    'generations': [{'generationId': name, 'pid': worker.process.pid,
                                     'bundleHash': worker.bundle_hash, 'busy': worker.busy,
                                     'retiring': worker.retiring, 'alive': worker.process.poll() is None}
                                    for name, worker in sorted(self._workers.items())]}

    def close(self):
        """Stop every owned child even after individual failures; idempotent shutdown."""
        with self._condition:
            self._closed = True
            self._active = None
            workers = list(self._workers.values())
            if self._starting is not None:
                workers.append(self._starting)
            self._workers.clear()
        failure = None
        for worker in workers:
            try:
                worker.stop()
            except Exception as error:
                if failure is None:
                    failure = error
                else:
                    failure.add_note(str(error))
        if failure is not None:
            raise failure
