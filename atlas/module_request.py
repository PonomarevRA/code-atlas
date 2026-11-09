"""HTTP cancellation ownership for bounded read-only module computations."""

import asyncio
from threading import Event

from .module_analysis import ModuleAnalysisError


async def run_module_request(request, operation, *, discard=None):
    """
    Run one pinned computation and propagate disconnect to its optional cooperative port.

    Args:
        request: Parsed Starlette request; its JSON body has already been consumed.
        operation: Trusted callable accepting a threading.Event and returning a bounded result.
        discard: Optional synchronous cleanup for a returned-but-undelivered retained state.
    Returns:
        The operation's result only while the HTTP caller is still connected.
    Raises:
        ModuleAnalysisError: Client disconnected (499); engine/store errors propagate unchanged.
        asyncio.CancelledError: Host request cancellation after owned work receives its stop event.
    """
    cancelled = Event()
    computation = asyncio.create_task(asyncio.to_thread(operation, cancelled))
    try:
        while not computation.done():
            await asyncio.wait({computation}, timeout=0.05)
            if await request.is_disconnected():
                cancelled.set()
        result = await asyncio.shield(computation)
        if cancelled.is_set() or await request.is_disconnected():
            raise ModuleAnalysisError('Module request was cancelled.', code='module_request_cancelled', status=499)
        return result
    except BaseException:
        cancelled.set()
        def discard_completed(future):
            """Own cleanup independently of repeated cancellation of the HTTP task."""
            try:
                value = future.result()
                if discard is not None:
                    discard(value)
            except (Exception, asyncio.CancelledError):
                # A failed computation has no delivered state to release. Cleanup
                # errors must not replace the original request/engine exception.
                pass
        computation.add_done_callback(discard_completed)
        try:
            await asyncio.shield(computation)
        except (Exception, asyncio.CancelledError):
            pass
        raise
