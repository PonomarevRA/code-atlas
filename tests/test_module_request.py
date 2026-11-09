"""Cancellation stops one pinned operation and discards undelivered state exactly once."""

import asyncio
from threading import Event

import pytest

from atlas.module_analysis import ModuleAnalysisError
from atlas.module_request import run_module_request


class Request:
    """Expose only a controlled disconnect signal, independent of any transport implementation."""
    def __init__(self, disconnected=False):
        self.disconnected = disconnected

    async def is_disconnected(self):
        return self.disconnected


def test_connected_request_returns_exact_result_without_cancel_or_discard():
    """No request-wide stop is invented for a normal successful consumer."""
    discarded = []
    def compute(cancelled):
        assert not cancelled.is_set()
        return {'value': 7}
    result = asyncio.run(run_module_request(Request(), compute, discard=discarded.append))
    assert result == {'value': 7} and not discarded


def test_disconnect_reaches_worker_and_discards_a_returned_handle_once():
    """A non-deliverable state cannot consume an invisible extra context indefinitely."""
    discarded = []
    def compute(cancelled):
        assert cancelled.wait(2)
        return {'stateRef': 'test-handle'}
    with pytest.raises(ModuleAnalysisError) as error:
        asyncio.run(run_module_request(Request(True), compute, discard=discarded.append))
    assert error.value.status == 499 and discarded == [{'stateRef': 'test-handle'}]


def test_host_cancellation_stops_only_its_owned_operation():
    """Cancellation propagation consumes the future while preserving the original CancelledError."""
    entered, stopped, other = Event(), Event(), Event()
    def compute(cancelled):
        entered.set()
        assert cancelled.wait(2)
        stopped.set()
        return {'cancelled': True}
    async def scenario():
        task = asyncio.create_task(run_module_request(Request(), compute))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert stopped.is_set() and not other.is_set()


def test_repeated_cancellation_keeps_exactly_once_discard_ownership():
    """A second HTTP cancellation cannot orphan a later retained result."""
    entered, release = Event(), Event()
    discarded = []
    def compute(cancelled):
        entered.set()
        assert release.wait(2)
        assert cancelled.is_set()
        return {'stateRef': 'late-handle'}
    async def scenario():
        task = asyncio.create_task(run_module_request(Request(), compute, discard=discarded.append))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        for _ in range(100):
            if discarded:
                break
            await asyncio.sleep(.01)
        assert discarded == [{'stateRef': 'late-handle'}]
    asyncio.run(scenario())
