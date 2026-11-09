"""Exercise real concurrent requests, cooperative stop ordering and safe cancellation evidence."""

import asyncio
import json
import logging
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from atlas.http_lifecycle import HTTPLifecycle, HTTPState
from atlas.local_http_boundary import SECURITY_HEADERS
from atlas.technical_logs.events import from_record


def test_stop_rejects_new_requests_but_admitted_sync_request_finishes():
    """An active thread retains resources while new HTTP work is rejected without reading its body."""
    release = Event()
    calls = []

    async def scenario():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        state = HTTPState()
        app = FastAPI()
        app.add_middleware(HTTPLifecycle, state=state)

        @app.get("/work/{identifier}")
        def work(identifier: str):
            """Wait in a real Starlette worker until the test allows the admitted operation to finish."""
            calls.append(identifier)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return {"identifier": identifier}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
            pending = asyncio.create_task(client.get("/work/first"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                state.request_stop()
                rejected = await client.get("/work/second")
                assert rejected.status_code == 503
                for name, value in SECURITY_HEADERS.items():
                    assert rejected.headers[name] == value
                assert not pending.done()
                assert calls == ["first"]
                release.set()
                response = await asyncio.wait_for(pending, 2)
                assert response.status_code == 200
                assert response.json() == {"identifier": "first"}
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelled_sync_request_logs_template_duration_and_preserves_cancellation(caplog):
    """The server sees the original cancellation; its journal gets neither actual IDs nor query secrets."""
    release = Event()

    async def scenario():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        app = FastAPI()
        app.add_middleware(HTTPLifecycle, state=HTTPState())

        @app.get("/api/projects/{project_id}/work")
        def work(project_id: str):
            """Keep a synchronous endpoint active at the same thread-pool await as the observed incident."""
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return {"id": project_id}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
            pending = asyncio.create_task(client.get("/api/projects/private-project/work?token=private-query"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                pending.cancel("original cancellation")
                with pytest.raises(asyncio.CancelledError, match="original cancellation"):
                    await pending
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)

    with caplog.at_level(logging.WARNING, logger="atlas.http_lifecycle"):
        asyncio.run(scenario())
    rows = [record for record in caplog.records if getattr(record, "eventName", None) == "http.request.cancelled"]
    assert len(rows) == 1
    event = from_record(rows[0])
    assert event["level"] == "WARN"
    assert event["properties"]["phase"] == "GET /api/projects/{project_id}/work"
    assert event["properties"]["durationMs"] >= 0
    assert event["properties"]["errorCode"] == "cancelled"
    assert event["exception"] is None
    raw = json.dumps(event)
    assert "private-project" not in raw and "private-query" not in raw


def test_unmatched_cancellation_does_not_log_raw_path_or_custom_method(caplog):
    """Cancellation before routing uses fixed placeholders instead of untrusted ASGI request values."""
    async def cancelled(scope, receive, send):
        """Simulate cancellation while the outer HTTP boundary is still receiving a request body."""
        raise asyncio.CancelledError("cancel")

    async def scenario():
        middleware = HTTPLifecycle(cancelled, HTTPState())
        with pytest.raises(asyncio.CancelledError, match="cancel"):
            await middleware({"type": "http", "method": "PRIVATE-METHOD", "path": "/private-path"}, None, None)

    with caplog.at_level(logging.WARNING, logger="atlas.http_lifecycle"):
        asyncio.run(scenario())
    event = from_record(caplog.records[-1])
    assert event["properties"]["phase"] == "UNKNOWN <unmatched>"
    assert "PRIVATE-METHOD" not in json.dumps(event)
    assert "private-path" not in json.dumps(event)


def test_stop_signals_once_and_concurrent_request_does_not_wait_for_signal():
    """Admission closes before callbacks and a concurrent stop does not wait for an existing callback."""
    entered, release = Event(), Event()
    calls = []
    state = HTTPState()

    def signal():
        """Hold a test callback to expose stop ordering without taking a real service lock."""
        assert state.stopping.is_set()
        calls.append("signal")
        entered.set()
        assert release.wait(5)

    state.callbacks = (signal,)
    worker = Thread(target=state.request_stop)
    worker.start()
    try:
        assert entered.wait(2)
        repeated = Thread(target=state.request_stop)
        repeated.start()
        repeated.join(1)
        assert not repeated.is_alive()
    finally:
        release.set()
        worker.join(2)
    state.request_stop()
    assert calls == ["signal"]


def test_create_app_stop_signals_workers_without_closing_live_resources(tmp_path, monkeypatch):
    """The explicit app callback closes admission before worker drain and leaves journal handles open."""
    from atlas.server import create_app

    app = create_app(tmp_path)
    monkeypatch.setattr(app.state.references, "start_initial_indexing", lambda: None)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/projects").status_code == 200
        app.state.request_shutdown()
        app.state.request_shutdown()
        assert app.state.references.closed
        assert app.state.research.closed
        assert app.state.remote_repositories.stop.is_set()
        assert not app.state.technical_logs.stopping.is_set()
        assert client.get("/api/projects").status_code == 503


def test_non_http_protocol_remains_available_while_stopping():
    """ASGI lifespan still reaches application cleanup after admission is stopped."""
    calls = []

    async def application(scope, receive, send):
        """Record protocol delegation without touching HTTP-only fields."""
        calls.append(scope["type"])

    state = HTTPState()
    state.request_stop()
    asyncio.run(HTTPLifecycle(application, state)({"type": "lifespan"}, None, None))
    assert calls == ["lifespan"]


def test_rejection_never_reads_body_and_signal_failure_does_not_skip_other_workers(caplog):
    """A broken optional signal cannot keep admission open, skip later signals or consume new input."""
    calls = []

    def broken():
        """Represent one faulty service signal without exposing its error payload in diagnostics."""
        raise RuntimeError("private failure details")

    state = HTTPState((broken, lambda: calls.append("stopped")))
    with caplog.at_level(logging.WARNING, logger="atlas.http_lifecycle"):
        state.request_stop()
    assert calls == ["stopped"]
    assert "private failure details" not in caplog.text

    async def forbidden(*args):
        """Neither request-body receive nor the application may run after admission closes."""
        pytest.fail("request admitted while stopping")

    async def scenario():
        messages = []

        async def send(message):
            """Collect the ASGI response without requiring an HTTP request body."""
            messages.append(message)

        await HTTPLifecycle(forbidden, state)({"type": "http", "method": "POST"}, forbidden, send)
        assert messages[0]["status"] == 503

    asyncio.run(scenario())
