"""Synthetic request lifetimes and fixed-state native projection; no user stores or browser."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from desktop_support.activity import RequestActivity, ActivityMiddleware, install_activity
from desktop_support.tray_activity import ActivityPoller, decode_activity
from desktop_support.tray_group import CATALOG


def test_concurrent_requests_and_recent_outcome_expire():
    """One completed request cannot conceal another active request; success expires after15seconds."""
    now = [0.0]
    tracker = RequestActivity('code-atlas', lambda: now[0])
    assert tracker.snapshot()['state'] == 'idle'
    tracker.begin(); tracker.begin()
    tracker.end(False)
    assert tracker.snapshot()['activeRequests'] == 1
    assert tracker.snapshot()['state'] == 'busy'
    tracker.end(True)
    assert tracker.snapshot()['state'] == 'error'
    now[0] = 15
    assert tracker.snapshot()['state'] == 'idle'
    assert set(tracker.snapshot()) == {'schemaVersion', 'appId', 'state', 'activeRequests', 'coverage'}


@pytest.mark.parametrize('failure', [None, RuntimeError, asyncio.CancelledError])
def test_asgi_lifetime_preserves_messages_exceptions_and_cancellation(failure):
    """The tracker observes dispatch and always releases its counter without replacing application errors."""
    tracker = RequestActivity('ag-tasks')
    message = {'type': 'http.response.start', 'status': 201}
    seen = []
    async def app(scope, receive, send):
        assert tracker.snapshot()['state'] == 'busy'
        await send(message)
        if failure:
            raise failure()
    async def send(value):
        seen.append(value)
    task = ActivityMiddleware(app, tracker)({'type': 'http', 'path': '/api/v1/agent/issues'}, None, send)
    if failure:
        with pytest.raises(failure):
            asyncio.run(task)
    else:
        asyncio.run(task)
    assert tracker.snapshot()['activeRequests'] == 0
    assert tracker.snapshot()['state'] == ('error' if failure else 'success')
    assert seen[0] is message


@pytest.mark.parametrize('path', ['/healthz', '/api/desktop/v1/activity', '/api/v1/status', '/api/agent-activity/events', '/api/v1/events'])
def test_polling_never_creates_its_own_activity(path):
    """Observability reads and streams cannot keep the indicator permanently blue."""
    tracker = RequestActivity('ag-design')
    async def app(scope, receive, send):
        assert tracker.snapshot()['state'] == 'idle'
    asyncio.run(ActivityMiddleware(app, tracker)({'type': 'http', 'path': path}, None, None))
    assert tracker.snapshot()['state'] == 'idle'


def test_endpoint_is_small_local_only_and_body_free():
    """Synthetic local requests get counts, while foreign origins and selector queries are refused."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    tracker = install_activity(app, 'code-atlas')
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 32100)) as client:
        tracker.begin()
        response = client.get('/api/desktop/v1/activity')
        assert response.json()['state'] == 'busy'
        assert len(response.content) < 512
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/api/desktop/v1/activity?token=private').status_code == 403
        assert client.get('/api/desktop/v1/activity', headers={'Origin': 'https://example.com'}).status_code == 403
        assert client.get('/api/desktop/v1/activity', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        assert client.get('/api/desktop/v1/activity', headers={'Host': 'example.com'}).status_code == 403


@pytest.mark.parametrize('value', [None, '1|evil|10|busy', '1|code-atlas|0|busy', '1|code-atlas|١|busy',
    '1|code-atlas|2147483648|busy', '1|code-atlas|10|secret', '1|code-atlas|10|busy|extra', 'x'*161])
def test_only_fixed_activity_wire_values_are_accepted(value):
    """Arbitrary application labels, PIDs, content and enum names never enter the native UI."""
    assert decode_activity(value, CATALOG) is None


def test_valid_activity_wire():
    assert decode_activity('1|code-atlas|123|busy', CATALOG) == ('code-atlas', 123, 'busy')


@pytest.mark.parametrize('origin', ['https://example.com', 'http://localhost:123', 'http://127.0.0.1:123/private',
    'http://user:token@127.0.0.1:123', 'http://127.0.0.1:123?token=private'])
def test_poller_refuses_external_credentials_or_path_selectors(origin):
    with pytest.raises(ValueError):
        ActivityPoller('code-atlas', origin)


def test_lost_peer_activity_is_removed_and_old_signal_is_stale():
    """A cached green/busy signal cannot survive membership loss or the freshness deadline."""
    from desktop_support.cocoa_tray import CocoaTray
    from time import monotonic
    tray = CocoaTray.__new__(CocoaTray)
    tray.group = SimpleNamespace(pid=1, peers={2: object()})
    tray.activity_states = {2: ('ag-tasks', 'busy', monotonic() - 8)}
    assert tray.state_for('ag-tasks') == 'stale'
    tray.group.peers.clear()
    assert tray.state_for('ag-tasks') == 'unknown'
    assert not tray.activity_states


def test_follower_rotation_cannot_grow_cached_activity():
    """A non-owner prunes1000 departed PIDs even without a visible native item."""
    from desktop_support.cocoa_tray import CocoaTray
    from time import monotonic
    tray = CocoaTray.__new__(CocoaTray)
    tray.item = None
    tray.group = SimpleNamespace(pid=1, peers={})
    tray.activity_states = {}
    for pid in range(2, 1002):
        tray.group.peers = {pid: object()}
        tray.activity_states[pid] = ('ag-tasks', 'busy', monotonic())
        tray.update_indicator()
        assert len(tray.activity_states) == 1


def test_offline_attempt_does_not_refresh_valid_receipt(monkeypatch):
    """A socket failure cannot keep resetting the freshness deadline."""
    import desktop_support.tray_activity as module
    from threading import Lock
    class StopAfterOne:
        def __init__(self):
            self.done = False
        def is_set(self):
            return self.done
        def wait(self, seconds):
            self.done = True
    class Offline:
        def open(self, *args, **kwargs):
            raise OSError('synthetic offline')
    poller = ActivityPoller.__new__(ActivityPoller)
    poller.lock = Lock()
    poller.latest = ('success', 0)
    poller.app_id, poller.origin = 'code-atlas', 'http://127.0.0.1:12345'
    poller.opener, poller.stopping = Offline(), StopAfterOne()
    monkeypatch.setattr(module, 'monotonic', lambda: 20)
    poller.run()
    assert poller.latest == ('success', 0)
    assert poller.read() == 'stale'
