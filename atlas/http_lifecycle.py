"""HTTP admission and cancellation diagnostics during bounded application shutdown."""

import asyncio
import logging
from threading import Event, Lock
import time

from starlette.responses import JSONResponse

from .local_http_boundary import SECURITY_HEADERS

LOGGER = logging.getLogger(__name__)
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}


class HTTPState:
    """Signal shutdown once without closing resources still used by admitted requests."""

    def __init__(self, callbacks=()):
        """Accept only nonblocking, idempotent cancellation signals; cleanup belongs to lifespan."""
        self.stopping = Event()
        self.lock = Lock()
        self.callbacks = tuple(callbacks)

    def request_stop(self):
        """Stop admission before signalling workers; repeated callers never wait for one another."""
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self.stopping.is_set():
                return
            self.stopping.set()
            for callback in self.callbacks:
                try:
                    callback()
                except Exception:
                    # A failed signal must not prevent other workers or lifespan cleanup from stopping.
                    try:
                        LOGGER.warning("Cooperative shutdown signal failed",
                                       extra={"eventName": "shutdown.signal.failed",
                                              "properties": {"component": "http", "phase": "stopping",
                                                             "errorCode": "signal_failed"}})
                    except Exception:
                        pass
        finally:
            self.lock.release()


class HTTPLifecycle:
    """Reject newly arriving requests after stop and explain cancellations without request data."""

    def __init__(self, app, state):
        """Share one state with the desktop stop callback and pass other ASGI protocols through."""
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        """Let admitted requests drain; propagate cancellation after recording its route and duration."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.state.stopping.is_set():
            response = JSONResponse({"error": "Atlas завершает работу. Повторите запрос после запуска."},
                                    status_code=503, headers=SECURITY_HEADERS)
            await response(scope, receive, send)
            return
        began = time.monotonic()
        try:
            await self.app(scope, receive, send)
        except asyncio.CancelledError:
            self._record_cancellation(scope, began)
            raise

    @staticmethod
    def _record_cancellation(scope, began):
        """Log only a declared route template and a known HTTP method, never raw URLs or headers."""
        method = scope.get("method")
        if method not in HTTP_METHODS:
            method = "UNKNOWN"
        template = getattr(scope.get("route"), "path", None)
        if type(template) is not str:
            template = "<unmatched>"
        template = template[:256]
        duration = max(0, (time.monotonic() - began) * 1000)
        try:
            LOGGER.warning("HTTP request cancelled: %s %s after %.1f ms", method, template, duration,
                           extra={"eventName": "http.request.cancelled",
                                  "properties": {"component": "http", "phase": method + " " + template,
                                                 "durationMs": duration, "errorCode": "cancelled"}})
        except Exception:
            # Diagnostics must not replace the cancellation driving server shutdown.
            pass
