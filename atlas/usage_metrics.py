"""Core-owned, process-lifetime telemetry; modules never instrument themselves or send private data."""
from contextlib import nullcontext
from threading import Lock
import time
from .usage_contract import BUCKETS, MAX_RECORDS, MAX_COUNT, OUTCOMES, IDENTIFIER, MOD_OPERATIONS, HTTP_OPERATIONS, CLIENTS, SCHEMA

def observe(target, method, *args, **kwargs):
    """Attempt optional telemetry; failures must not change a product result, exception or cancellation."""
    if target is None:
        return None
    try:
        return getattr(target, method)(*args, **kwargs)
    except Exception:
        return None

class Measurement:
    """One actual callback invocation; failures and cancellation release in-flight counts exactly once."""

    def __init__(self, registry, key):
        self.registry, self.key, self.began = (registry, key, time.perf_counter())
        self.outcome = 'success'
        self.closed = False
        self.lock = Lock()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        outcome = self.outcome
        if kind is not None:
            outcome = 'cancelled' if kind.__name__ == 'CancelledError' else 'error'
        self.finish(outcome)
        return False

    def finish(self, outcome='success', facts=(), *, seconds=None):
        """Finish an invocation once; retain only known confidence counts, never fact contents."""
        with self.lock:
            if self.closed:
                return
            self.closed = True
        try:
            self.registry.finish(self.key, outcome, max(0, time.perf_counter() - self.began) if seconds is None else seconds, facts)
        except Exception:
            # Optional counters must not replace the callback result or original exception.
            pass

class UsageMetrics:
    """Bound aggregate storage independently of projects, sessions, package versions and request count."""

    def __init__(self):
        self.lock = Lock()
        self.started = time.time()
        self.rows = {}
        self.dropped = 0
        self.http_active = 0

    @staticmethod
    def mod_id(provider):
        """Strip an internal provider suffix; invalid metadata is never exported as a label."""
        if not isinstance(provider, str):
            return None
        value = provider.split('/', 1)[0]
        return value if IDENTIFIER.fullmatch(value) else None

    def begin(self, mod, operation, kind='mod', client=''):
        """Reserve one bounded aggregate row before a real callback starts."""
        identity = self.mod_id(mod) if kind != 'http' else ''
        operations = HTTP_OPERATIONS if kind == 'http' else MOD_OPERATIONS
        clients = CLIENTS if kind == 'http' else ('',)
        if (kind not in {'mod', 'http', 'cache', 'selection'} or identity is None
                or operation not in operations or client not in clients):
            return None
        key = (kind, identity, operation, client)
        with self.lock:
            if key not in self.rows:
                if len(self.rows) >= MAX_RECORDS:
                    self.dropped = min(MAX_COUNT, self.dropped + 1)
                    return None
                self.rows[key] = {'kind': kind, 'mod': identity, 'operation': operation, 'client': client, 'count': 0, 'outcomes': dict.fromkeys(OUTCOMES, 0), 'inFlight': 0, 'seconds': 0.0, 'buckets': [0] * (len(BUCKETS) + 1), 'lastCompleted': 0.0, 'facts': dict.fromkeys(('confirmed_static', 'candidate', 'unknown'), 0)}
            self.rows[key]['inFlight'] = min(MAX_COUNT, self.rows[key]['inFlight'] + 1)
        return Measurement(self, key)

    def operation(self, mod, operation):
        """Use a no-op context when identity/cardinality is unsupported; product behavior stays unchanged."""
        return observe(self, 'begin', mod, operation) or nullcontext()

    def finish(self, key, outcome, seconds, facts=()):
        """Update a completed observation under one lock, with seconds buckets and saturating counters."""
        if outcome not in OUTCOMES:
            outcome = 'error'
        seconds = min(1e+18, max(0, seconds))
        with self.lock:
            row = self.rows[key]
            row['inFlight'] = max(0, row['inFlight'] - 1)
            if row['count'] >= MAX_COUNT:
                self.dropped = min(MAX_COUNT, self.dropped + 1)
                return
            row['count'] += 1
            row['outcomes'][outcome] += 1
            row['seconds'] = min(1e+18, row['seconds'] + seconds)
            row['lastCompleted'] = time.time()
            bucket = next((i for i, edge in enumerate(BUCKETS) if seconds <= edge), len(BUCKETS))
            row['buckets'][bucket] += 1
            for fact in facts:
                confidence = getattr(fact, 'confidence', 'unknown').replace('-', '_')
                if confidence in row['facts']:
                    row['facts'][confidence] = min(MAX_COUNT, row['facts'][confidence] + 1)

    def cache_hit(self, provider):
        """Count a discovery-cache hit separately from an actual compiler probe."""
        sample = observe(self, 'begin', provider, 'semantic_probe', kind='cache')
        if sample:
            sample.finish('cache_hit')

    def rule_pass(self, mod):
        """Record declarative participation in a source pass, explicitly separate from executed mod calls."""
        sample = observe(self, 'begin', mod, 'source_rules', kind='selection')
        if sample:
            sample.finish()

    def http_change(self, delta):
        """Track active API requests independently of completed series and their cardinality cap."""
        with self.lock:
            self.http_active = min(MAX_COUNT, max(0, self.http_active + delta))

    def snapshot(self):
        """Copy a bounded scalar DTO without walking files, reading settings or invoking modules."""
        from copy import deepcopy
        with self.lock:
            return {'schemaVersion': SCHEMA, 'startedAt': self.started, 'httpInFlight': self.http_active, 'droppedObservations': self.dropped, 'records': deepcopy(list(self.rows.values()))}

class UsageMiddleware:
    """Measure matched API categories and declared MCP client kinds without reading request bodies."""

    def __init__(self, app, metrics):
        self.app, self.metrics = (app, metrics)

    async def __call__(self, scope, receive, send):
        """Forward the original ASGI stream while observing only bounded route/client categories."""
        path = scope.get('path', '')
        if scope['type'] != 'http' or not path.startswith('/api/') or path.startswith(('/api/diagnostics/', '/api/agent-activity', '/api/technical-logs', '/api/desktop/')):
            await self.app(scope, receive, send)
            return
        from starlette.datastructures import Headers
        headers = Headers(scope=scope)
        session = headers.get('x-atlas-mcp-session', '')
        client = 'http'
        if len(session) == 32 and all((c in '0123456789abcdef' for c in session)):
            declared = headers.get('x-atlas-mcp-client', '')
            client = declared if declared in {'codex', 'cursor', 'claude'} else 'mcp_unknown'
        start = time.perf_counter()
        status = 500
        outcome = 'error'

        async def observed_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)
        observe(self.metrics, 'http_change', 1)
        try:
            await self.app(scope, receive, observed_send)
            outcome = 'success' if status < 400 else 'error'
        except BaseException as error:
            if type(error).__name__ == 'CancelledError':
                outcome = 'cancelled'
            raise
        finally:
            observe(self.metrics, 'http_change', -1)
            template = getattr(scope.get('route'), 'path', '')
            category = 'other'
            for marker, name in [('semantic', 'semantic'), ('code-health', 'health'), ('document', 'document'), ('browser', 'browser'), ('node', 'node'), ('projects', 'map')]:
                if marker in template:
                    category = name
                    break
            sample = observe(self.metrics, 'begin', '', category, kind='http', client=client)
            observe(sample, 'finish', outcome, seconds=time.perf_counter() - start)
