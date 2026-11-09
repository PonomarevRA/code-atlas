"""Exercise the native boundary without requiring OS widgets in the test runner."""

import base64

from atlas import desktop_automation_host as host


def fake_window(monkeypatch, url, responses):
    """Drive the same dispatch contract using a deterministic native view."""
    window = host.bind_window(object(), "http://127.0.0.1:8123")
    scripts = []
    monkeypatch.setattr(window, "_dispatch", lambda action: action())
    monkeypatch.setattr(window, "_view_and_url", lambda: ("view", url))

    def evaluate(_view, script, done):
        scripts.append(script)
        done(responses.pop(0))

    monkeypatch.setattr(window, "_evaluate", evaluate)
    return window, scripts


def test_owned_origin_and_bounded_command_results(monkeypatch):
    window, scripts = fake_window(monkeypatch, "http://127.0.0.1:8123/?repo=p", [
        '{"status":"pending"}', '{"status":"ok","state":{"projectId":"p"}}',
    ])
    result = window("execute", {"action": "filter_history", "query": '"} catch <script>'})
    assert result == {"status": "ok", "state": {"projectId": "p"}}
    assert len(scripts) == 2
    assert all('location.origin !== "http://127.0.0.1:8123"' in item for item in scripts)
    assert r'\"} catch <script>' in scripts[0]

    outside, scripts = fake_window(monkeypatch, "http://127.0.0.1:8111/", [])
    assert outside("inspect", {}) == {"status": "error", "error": "window_origin_mismatch"}
    assert not scripts


def test_capture_returns_bounded_png():
    data = b"\x89PNG\r\n\x1a\n"
    assert base64.b64decode(host._capture_result(data, "wkwebview")["pngBase64"]) == data
    assert host._capture_result(data + bytes(host._CAPTURE_LIMIT), "wkwebview")["error"] == "capture_too_large"
    assert host._capture_result(b"", "wkwebview")["error"] == "capture_failed"


def test_return_to_map_waits_for_committed_snapshot(monkeypatch):
    window, _ = fake_window(monkeypatch, "http://127.0.0.1:8123/wiki.html", [])
    state = {"route": {"path": "/"}, "projectId": "p", "snapshotId": "s"}
    results = iter([
        {"status": "ok", "navigation": {"path": "/"}},
        {"route": {"path": "/"}, "projectId": "p", "snapshotId": None},
        state,
    ])
    monkeypatch.setattr(window, "_command", lambda *_: next(results))
    assert window("execute", {"action": "return_to_map", "projectId": "p", "snapshotId": "s"}) == {
        "status": "ok", "state": state,
    }


def test_capture_rejects_scope_change_before_releasing_bytes(monkeypatch):
    window, _ = fake_window(monkeypatch, "http://127.0.0.1:8123/?repo=p", [])
    states = iter([
        {"projectId": "p", "snapshotId": "s", "revision": {"document": "same", "map": 1}},
        {"projectId": "p", "snapshotId": "s", "revision": {"document": "same", "map": 3}},
    ])
    monkeypatch.setattr(window, "_command", lambda *_: next(states))
    monkeypatch.setattr(host.sys, "platform", "darwin")
    monkeypatch.setattr(host, "_macos_capture", lambda _, done: done({"status": "ok", "pngBase64": "image"}))
    assert window("capture", {"projectId": "p", "snapshotId": "s"}) == {
        "status": "error", "error": "window_scope_changed",
    }
