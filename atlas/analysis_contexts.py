"""Task-scoped bounded RAM residency with idle expiry and single-flight computation."""

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Event, RLock, Thread
import time
from uuid import uuid4


class AnalysisContextError(RuntimeError):
    """A controlled scope, expiry or capacity error with an HTTP status."""

    def __init__(self, message: str, status: int = 410):
        """Preserve a safe error message and transport status for the caller."""
        super().__init__(message)
        self.status = status


@dataclass
class _Context:
    """One task owns its entries; a retired in-flight context cannot rejoin the store."""
    project: str
    snapshot: str
    settings: str
    deadline: float
    lock: object = field(default_factory=RLock)
    values: OrderedDict = field(default_factory=OrderedDict)
    bytes: int = 0
    units: int = 0


class AnalysisContexts:
    """Retain task data under source-byte/object-count quotas, not an asserted native RSS limit."""

    def __init__(self, *, clock=time.monotonic, idle_seconds=1200, max_contexts=8,
                 max_bytes=64 * 1024 * 1024, max_context_bytes=16 * 1024 * 1024,
                 max_units=200_000, max_entries=64):
        """
        Create idle handles without starting a thread or touching storage.

        Byte charges describe retained source/JSON, units bound retained AST
        nodes. A request may compute a larger uncached value. Native allocator
        overhead is measured separately; these quotas do not bound process RSS.
        """
        if min(idle_seconds, max_contexts, max_bytes, max_context_bytes, max_units, max_entries) <= 0:
            raise ValueError('Analysis context limits must be positive')
        self.clock, self.idle_seconds = clock, idle_seconds
        self.max_contexts, self.max_bytes = max_contexts, max_bytes
        self.max_context_bytes, self.max_units = max_context_bytes, max_units
        self.max_entries = max_entries
        self.lock, self.contexts = RLock(), OrderedDict()
        self.stop, self.thread, self.closed = Event(), None, False

    def start(self):
        """Start one lifecycle-owned sweeper; read checks enforce expiry between sweeps."""
        with self.lock:
            if not self.closed and self.thread is None:
                self.thread = Thread(target=self._sweep, name='atlas-analysis-expiry', daemon=True)
                self.thread.start()

    def _sweep(self):
        """Release idle scopes without refreshing them through background activity."""
        while not self.stop.wait(5):
            with self.lock:
                self._expire()

    def _expire(self):
        """Drop expired ownership; in-flight local references die with their request."""
        now = self.clock()
        for key, context in list(self.contexts.items()):
            if context.deadline <= now:
                del self.contexts[key]

    def open(self, project: str, snapshot: str, settings: str = '') -> dict:
        """Allocate a new opaque task scope or reject capacity without evicting another task."""
        with self.lock:
            self._expire()
            if self.closed:
                raise AnalysisContextError('Analysis service is closed', 503)
            if len(self.contexts) >= self.max_contexts:
                raise AnalysisContextError('Analysis context capacity reached; close an unused context', 429)
            identifier = uuid4().hex
            self.contexts[identifier] = _Context(project, snapshot, settings, self.clock() + self.idle_seconds)
            return {'contextId': identifier, 'snapshotId': snapshot, 'idleTimeoutSeconds': self.idle_seconds,
                    'storage': 'memory-only', 'maxContextBytes': self.max_context_bytes,
                    'maxRetainedAstNodes': self.max_units}

    def _context(self, identifier, project, snapshot, settings):
        """Check actual caller scope under the registry lock, independent of selector checksums."""
        self._expire()
        context = self.contexts.get(identifier)
        if self.closed or context is None:
            raise AnalysisContextError('Analysis context expired or closed; open a new context')
        if (context.project, context.snapshot, context.settings) != (project, snapshot, settings):
            raise AnalysisContextError('Analysis context revision or settings changed', 409)
        return context

    def check(self, identifier, project, snapshot, settings=''):
        """Validate a context without extending its idle lifetime, including status polling."""
        with self.lock:
            self._context(identifier, project, snapshot, settings)

    def touch(self, identifier, project, snapshot, settings=''):
        """Extend idle residency only after the transport has produced a successful useful read."""
        with self.lock:
            context = self._context(identifier, project, snapshot, settings)
            context.deadline = self.clock() + self.idle_seconds
            self.contexts.move_to_end(identifier)

    def value(self, identifier, project, snapshot, settings, key, factory, charge):
        """
        Return one immutable cached value, coalescing same-context concurrent work.

        factory runs outside the registry lock. charge(value) returns retained
        bytes and AST units. Expiry/close during computation rejects publication;
        exceptions and oversized values are never retained. Caller must touch only
        after all source/snapshot checks and response-budget validation succeed.
        """
        with self.lock:
            context = self._context(identifier, project, snapshot, settings)
        with context.lock:
            with self.lock:
                if self._context(identifier, project, snapshot, settings) is not context:
                    raise AnalysisContextError('Analysis context was replaced')
                cached = context.values.get(key)
                if cached is not None:
                    context.values.move_to_end(key)
                    return cached[0]
            value = factory()
            size, units = charge(value)
            with self.lock:
                if self._context(identifier, project, snapshot, settings) is not context:
                    raise AnalysisContextError('Analysis context was retired during analysis')
                if value is None or size > self.max_context_bytes or units > self.max_units:
                    return value
                while context.values and (len(context.values) >= self.max_entries
                                          or context.bytes + size > self.max_context_bytes
                                          or context.units + units > self.max_units):
                    _, (_, old_bytes, old_units) = context.values.popitem(last=False)
                    context.bytes -= old_bytes
                    context.units -= old_units
                # Cross-task admission never steals an actively owned result.
                if sum(item.bytes for item in self.contexts.values()) + size > self.max_bytes:
                    return value
                context.values[key] = (value, size, units)
                context.bytes += size
                context.units += units
                return value

    def release(self, identifier, project, snapshot, settings=''):
        """Release only the selected caller scope; other tasks remain independent."""
        with self.lock:
            self._expire()
            context = self.contexts.get(identifier)
            if context is None:
                raise AnalysisContextError('Analysis context expired or closed')
            if (context.project, context.snapshot) != (project, snapshot):
                raise AnalysisContextError('Analysis context belongs to another scope', 409)
            del self.contexts[identifier]

    def invalidate_projects(self, project_ids):
        """
        Retire cached task scopes after an explicitly confirmed map cleanup.

        Args:
            project_ids: Registered selected code projects; other tasks and repositories remain unchanged.
        Returns:
            Number of retired contexts; late factories cannot publish through retired handles.
        """
        selected = set(project_ids)
        with self.lock:
            identifiers = [key for key, context in self.contexts.items() if context.project in selected]
            for key in identifiers:
                del self.contexts[key]
            return len(identifiers)

    def close(self):
        """Stop expiry and retire all scopes; late computations cannot republish."""
        with self.lock:
            self.closed = True
            self.contexts.clear()
            self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)


def ast_units(tree, maximum=200_000):
    """Count native nodes without creating a Python index; stop once retention is impossible."""
    if tree is None:
        return 0
    cursor, count = tree.root_node.walk(), 0
    while True:
        count += 1
        if count > maximum:
            return count
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return count
