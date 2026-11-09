"""
One admission boundary for map requests, queued workers and short cleanup commits.
"""
from contextlib import contextmanager
from functools import wraps
import asyncio
import inspect
from types import FunctionType
from fastapi.routing import APIRoute
from threading import Lock
from starlette.responses import JSONResponse


class MaintenanceBusy(RuntimeError):
    """
    A map operation cannot enter while a confirmed cleanup owns the admission boundary.
    """


class MapMaintenance:
    """
    Count admitted requests and queued/running futures without holding a lock during their work.
    """
    def __init__(self):
        """
        Create an idle process-local boundary; the existing server ProcessLock owns cross-process exclusion.
        """
        self.lock = Lock()
        self.requests = self.workers = 0
        self.committing = self.degraded = False

    def enter(self, *, worker=False):
        """
        Admit one request or queued worker and return an exactly-once release callback.

        Args:
            worker: True for a future from submission through completion, including followups/finally.
        Returns:
            Callback releasing this admission; cancellation and failed submission may call it safely.
        Raises:
            MaintenanceBusy: A commit or failed recovery currently blocks map access.
        """
        field = 'workers' if worker else 'requests'
        with self.lock:
            if self.committing or self.degraded:
                raise MaintenanceBusy('Map storage maintenance is in progress.')
            setattr(self, field, getattr(self, field) + 1)
        released = False
        def release(*_):
            """
            Release only this admission, including future callbacks invoked synchronously on cancellation.
            """
            nonlocal released
            with self.lock:
                if not released:
                    setattr(self, field, getattr(self, field) - 1)
                    released = True
        return release

    @contextmanager
    def exclusive(self):
        """
        Enter a nonblocking commit only after every admitted request and queued/running worker drained.

        Yields:
            None; no filesystem or service lock is held by this gate itself.
        Raises:
            MaintenanceBusy: Any admitted work, concurrent cleanup or failed recovery remains.
        """
        with self.lock:
            if self.committing or self.degraded or self.requests or self.workers:
                raise MaintenanceBusy('Wait for active map requests and analysis jobs to finish.')
            self.committing = True
        try:
            yield
        finally:
            with self.lock:
                self.committing = False


def map_operation(function):
    """
    Admit a direct store operation before any state mutation or source/snapshot read begins.

    Args:
        function: Host store method whose instance owns the application maintenance gate.
    Returns:
        Signature-preserving wrapper; nested HTTP/worker admissions remain safe and nonblocking.
    """
    @wraps(function)
    def admitted(self, *args, **kwargs):
        """
        Hold one admission through the complete direct method operation.

        Args:
            self: Existing store instance with a maintenance boundary.
            args: Original positional arguments forwarded unchanged.
            kwargs: Original keyword arguments forwarded unchanged.
        Returns:
            Original method result after releasing only this admission.
        Raises:
            MaintenanceBusy: Before any original method code runs during cleanup.
        """
        release = self.maintenance.enter()
        try:
            return function(self, *args, **kwargs)
        finally:
            release()
    return admitted


class GuardedExecutor:
    """
    Keep queued and running futures inside the shared boundary, including a worker's final cleanup.
    """
    def __init__(self, executor, maintenance):
        """
        Wrap a host-owned executor without creating a new pool.

        Args:
            executor: Existing executor implementing submit/shutdown.
            maintenance: Shared MapMaintenance instance used by HTTP admission and cleanup commits.
        """
        self.executor, self.maintenance = executor, maintenance

    def submit(self, function, *args, **kwargs):
        """
        Admit work before enqueueing so queued jobs cannot disappear from the cleanup exclusion test.

        Args:
            function: Trusted host worker; analysed project code is never dispatched here.
            args: Original positional worker arguments.
            kwargs: Original keyword worker arguments.
        Returns:
            Original Future, releasing its admission on success, error or cancellation.
        Raises:
            MaintenanceBusy: A cleanup commit owns admission.
            Exception: Original executor submission errors, after releasing the admission.
        """
        release = self.maintenance.enter(worker=True)
        try:
            future = self.executor.submit(function, *args, **kwargs)
        except BaseException:
            release()
            raise
        future.add_done_callback(release)
        return future

    def shutdown(self, *args, **kwargs):
        """
        Forward host shutdown; cancelled queued futures release their own admissions.

        Args:
            args: Existing executor positional shutdown options.
            kwargs: Existing executor keyword shutdown options.
        Returns:
            The wrapped executor's shutdown result.
        """
        return self.executor.shutdown(*args, **kwargs)


def guard_executor(executor, maintenance):
    """
    Wrap one existing pool once, rejecting accidental attachment to a different application boundary.

    Args:
        executor: Existing host executor or its already guarded wrapper.
        maintenance: Application-owned MapMaintenance instance.
    Returns:
        A GuardedExecutor sharing exactly this maintenance boundary.
    Raises:
        ValueError: A wrapped pool belongs to another application boundary.
    """
    if isinstance(executor, GuardedExecutor):
        if executor.maintenance is not maintenance:
            raise ValueError('Executor belongs to a different maintenance scope')
        return executor
    return GuardedExecutor(executor, maintenance)


def _track_endpoint(endpoint, maintenance):
    """
    Retain admission inside the actual synchronous endpoint thread until its body really returns.

    Args:
        endpoint: Trusted synchronous FastAPI endpoint registered by the host application.
        maintenance: Application admission gate; no project-supplied callable is accepted.
    Returns:
        Signature/annotation-preserving host wrapper with actual thread-lifetime ownership.
    """
    def invoke(*args, **kwargs):
        """
        Execute one admitted host endpoint even if its awaiting HTTP task is cancelled.

        Args:
            args: Original host endpoint positional arguments.
            kwargs: Original host endpoint keyword arguments.
        Returns:
            Original endpoint result; admission releases only after the thread exits this body.
        """
        release = maintenance.enter()
        try:
            return endpoint(*args, **kwargs)
        finally:
            release()
    # FastAPI resolves postponed annotations in the callable's globals. Preserve the trusted
    # original module namespace without evaluating an annotation or mutating global dictionaries.
    bound = FunctionType(invoke.__code__, endpoint.__globals__, endpoint.__name__, invoke.__defaults__, invoke.__closure__)
    return wraps(endpoint)(bound)


def maintenance_route_class(maintenance):
    """
    Supply a route class that protects real synchronous endpoint bodies from cancellation abandonment.

    Args:
        maintenance: Application gate shared with middleware, stores and queued worker executors.
    Returns:
        APIRoute subclass for host routes registered after FastAPI application construction.
    """
    class MaintenanceRoute(APIRoute):
        """
        Attach ownership at sync execution, independent of ASGI request-task cancellation semantics.
        """
        def __init__(self, path, endpoint, **kwargs):
            """
            Wrap only map-capable API bodies; cleanup/status endpoints remain independent.

            Args:
                path: Declared host route template, never an arbitrary client filesystem path.
                endpoint: Original trusted FastAPI endpoint function.
                kwargs: Unchanged host route options forwarded to APIRoute.
            """
            exempt = path.startswith('/api/technical/map-cleanup') or path == '/api/technical/storage'
            if path.startswith('/api/') and not exempt and not inspect.iscoroutinefunction(endpoint):
                endpoint = _track_endpoint(endpoint, maintenance)
            super().__init__(path, endpoint, **kwargs)
    return MaintenanceRoute


class MapMaintenanceHTTP:
    """
    Protect entire HTTP responses, including streaming reads, without awaiting under a thread lock.
    """
    def __init__(self, app, maintenance):
        """
        Bind the application ASGI handler to the same gate used by every map worker.

        Args:
            app: Wrapped ASGI handler.
            maintenance: Application-owned MapMaintenance instance.
        """
        self.app, self.maintenance = app, maintenance

    async def __call__(self, scope, receive, send):
        """
        Count admitted API work until response completion; keep cleanup/status and static assets usable.

        Args:
            scope: Standard ASGI scope; only fixed public path categories bypass admission.
            receive: ASGI request receiver forwarded unchanged.
            send: ASGI response sender forwarded unchanged.
        Returns:
            None after the complete response; busy requests receive an explicit409 code.
        """
        path = scope.get('path', '')
        exempt = path.startswith('/api/technical/map-cleanup') or path == '/api/technical/storage'
        if scope['type'] != 'http' or not path.startswith('/api/') or exempt:
            await self.app(scope, receive, send)
            return
        try:
            release = self.maintenance.enter()
        except MaintenanceBusy as error:
            response = JSONResponse({'error': str(error), 'code': 'CLEANUP_BUSY', 'retryable': True}, status_code=409)
            await response(scope, receive, send)
            return
        pending = asyncio.create_task(self.app(scope, receive, send))
        def completed(task):
            """
            Release response ownership only after the protected ASGI task actually finishes.

            Args:
                task: Original shielded application task; cancelled client waits do not cancel it.
            """
            release()
            if not task.cancelled():
                task.exception()  # Retrieve disconnected-response failures without hiding awaited errors.
        pending.add_done_callback(completed)
        # asyncio cancellation can abandon an awaiting sync thread. Shield the application task;
        # host sync endpoints additionally own their actual thread through MaintenanceRoute.
        await asyncio.shield(pending)

