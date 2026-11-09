"""
Admission tracks actual thread/response/future lifetimes, including cancelled HTTP requests.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
import httpx
import pytest

from atlas.map_maintenance import (MapMaintenance, MapMaintenanceHTTP, MaintenanceBusy,
                                  GuardedExecutor, maintenance_route_class)
from atlas.map_cleanup_inventory import CleanupError
from test_map_cleanup import fixture, PROJECT


def test_queued_future_and_worker_finally_exclude_commit():
    """
    Queued work and a terminal-labelled worker still executing finally retain their admissions.
    """
    gate = MapMaintenance(); entered, release = Event(), Event()
    executor = GuardedExecutor(ThreadPoolExecutor(max_workers=1), gate)
    def worker():
        """
        Simulate a completed publication whose followup/finally still owns storage.
        """
        entered.set()
        assert release.wait(5)
    try:
        future = executor.submit(worker); assert entered.wait(2)
        queued = executor.submit(lambda: None)
        with pytest.raises(MaintenanceBusy):
            with gate.exclusive():pass
        assert queued.cancel()
        assert gate.workers == 1
        release.set(); future.result(timeout=2)
        deadline = time.monotonic()+2
        while gate.workers and time.monotonic()<deadline:time.sleep(.005)
        with gate.exclusive():pass
    finally:
        release.set();executor.shutdown(wait=True,cancel_futures=True)


def test_direct_store_mutation_is_rejected_before_catalog_write(fixture):
    """
    A direct method call cannot bypass HTTP admission and mutate a catalog before submit rejection.

    Args:
        fixture: Isolated real code/data stores and cleanup service.
    """
    code,data,cleanup,source,directory=fixture
    before=code.registry.read_bytes()
    with code.maintenance.exclusive():
        with pytest.raises(MaintenanceBusy):code.rebuild(PROJECT)
        with pytest.raises(MaintenanceBusy):code.comparison_snapshot(PROJECT)
    assert code.registry.read_bytes()==before and code.active_job is None
    assert code.projects[PROJECT]['status']=='completed'


@pytest.mark.parametrize('tracked_route',[False,True])
def test_cancelled_http_wait_does_not_release_running_sync_handler(tracked_route):
    """
    Cancel the HTTP waiter while an actual sync endpoint thread continues; cleanup stays excluded.

    Args:
        tracked_route: Exercise middleware alone and the full application sync-route ownership layer.
    """
    gate=MapMaintenance();entered,release,finished=Event(),Event(),Event()
    app=FastAPI()
    if tracked_route:app.router.route_class=maintenance_route_class(gate)
    @app.get('/api/blocked')
    def blocked():
        """
        Keep one real synchronous endpoint alive after client cancellation.
        """
        entered.set()
        try:
            assert release.wait(5)
            return {'ok':True}
        finally:finished.set()
    app.add_middleware(MapMaintenanceHTTP,maintenance=gate)
    async def scenario():
        """
        Cancel only the client task, preserving ownership until the server thread actually finishes.
        """
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            request=asyncio.create_task(client.get('/api/blocked'))
            assert await asyncio.to_thread(entered.wait,2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):await request
            assert not finished.is_set()
            with pytest.raises(MaintenanceBusy):
                with gate.exclusive():pass
            release.set()
            assert await asyncio.to_thread(finished.wait,2)
            for _ in range(100):
                if not gate.requests:break
                await asyncio.sleep(.005)
            assert gate.requests==0
            with gate.exclusive():pass
    try:asyncio.run(scenario())
    finally:release.set()


def test_cancelled_http_keeps_actual_snapshot_read_owned(fixture,monkeypatch):
    """
    A blocked real ProjectStore snapshot reader cannot overlap confirmed cleanup after cancellation.

    Args:
        fixture: Isolated real CAS snapshot and cleanup service.
        monkeypatch: Wrap only the fixture's existing graph reader with a controlled delay.
    """
    code,data,cleanup,source,directory=fixture
    plan=cleanup.preview([{'kind':'code','id':PROJECT}])
    entered,release,finished=Event(),Event(),Event()
    original=code._read_graph
    def blocked(path):
        """
        Hold the existing reader while preserving its complete parsed-graph behavior.

        Args:
            path: Original server-owned snapshot path.
        Returns:
            The real fixture graph after releasing the controlled read delay.
        """
        entered.set()
        try:
            assert release.wait(5)
            return original(path)
        finally:finished.set()
    monkeypatch.setattr(code,'_read_graph',blocked)
    app=FastAPI();app.router.route_class=maintenance_route_class(code.maintenance)
    @app.get('/api/read')
    def read():
        """
        Exercise the actual supported store reader rather than a synthetic gate counter.
        """
        return code.comparison_snapshot(PROJECT)[1]
    app.add_middleware(MapMaintenanceHTTP,maintenance=code.maintenance)
    async def scenario():
        """
        Verify cancellation cannot authorize cleanup while the original snapshot file is still read.
        """
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            request=asyncio.create_task(client.get('/api/read'))
            assert await asyncio.to_thread(entered.wait,2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):await request
            with pytest.raises(CleanupError) as error:cleanup.execute(plan['planId'])
            assert error.value.code=='CLEANUP_BUSY' and directory.exists()
            release.set();assert await asyncio.to_thread(finished.wait,2)
            for _ in range(100):
                if not code.maintenance.requests:break
                await asyncio.sleep(.005)
    try:asyncio.run(scenario())
    finally:release.set()


def test_file_response_handle_survives_cancel_until_real_stream_exit(tmp_path,monkeypatch):
    """
    Keep admission through the actual FileResponse file context, beyond endpoint return/cancellation.

    Args:
        tmp_path: Private file to stream; no real map or user data is opened.
        monkeypatch: Delay only asynchronous file reads while retaining the real file handle lifecycle.
    """
    import starlette.responses as responses
    gate=MapMaintenance();entered,release,closed=Event(),Event(),Event()
    path=tmp_path/'map.json';path.write_text('{"fixture":true}')
    original=responses.anyio.open_file
    async def open_file(*args,**kwargs):
        """
        Wrap a real async file without replacing filesystem contents or close semantics.

        Args:
            args: Original FileResponse open arguments.
            kwargs: Original FileResponse open options.
        Returns:
            Async context retaining the actual file and a controlled read delay.
        """
        handle=await original(*args,**kwargs)
        class DelayedFile:
            """
            Keep the real descriptor open until the response finishes reading or closes it.
            """
            async def __aenter__(self):
                """
                Enter the real file context and return the delayed reader.
                """
                await handle.__aenter__();return self
            async def __aexit__(self,*exc):
                """
                Close the real descriptor and publish the test observation.

                Args:
                    exc: Original async context exit arguments.
                """
                try:return await handle.__aexit__(*exc)
                finally:closed.set()
            async def read(self,size):
                """
                Delay a real bounded read while the server must retain storage ownership.

                Args:
                    size: Original FileResponse chunk size.
                Returns:
                    Actual bytes read from the private file.
                """
                entered.set();assert await asyncio.to_thread(release.wait,5)
                return await handle.read(size)
        return DelayedFile()
    monkeypatch.setattr(responses.anyio,'open_file',open_file)
    app=FastAPI();app.router.route_class=maintenance_route_class(gate)
    @app.get('/api/file')
    def file():
        """
        Return the actual FileResponse whose read occurs after this sync endpoint has returned.
        """
        return FileResponse(path)
    app.add_middleware(MapMaintenanceHTTP,maintenance=gate)
    async def scenario():
        """
        Cancel the waiter, then require the real file context to close before cleanup admission.
        """
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            request=asyncio.create_task(client.get('/api/file'))
            assert await asyncio.to_thread(entered.wait,2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):await request
            assert not closed.is_set()
            with pytest.raises(MaintenanceBusy):
                with gate.exclusive():pass
            release.set();assert await asyncio.to_thread(closed.wait,2)
            for _ in range(100):
                if not gate.requests:break
                await asyncio.sleep(.005)
            with gate.exclusive():pass
    try:asyncio.run(scenario())
    finally:release.set()
