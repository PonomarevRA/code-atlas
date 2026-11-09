"""Автооткрытие не должно опережать сервер и запускаться для служебных CLI команд."""
from types import SimpleNamespace
from threading import Event
from atlas import browser_launch, cli


def test_opens_ready_server_once(monkeypatch):
    """Готовый сервер открывает ровно одну вкладку."""
    calls = []
    monkeypatch.setattr(browser_launch, "open_browser", calls.append)
    browser_launch.open_when_ready(SimpleNamespace(started=True, should_exit=False), Event(), "http://127.0.0.1:9876")
    assert calls == ["http://127.0.0.1:9876"]


def test_failed_or_stopped_server_does_not_open(monkeypatch):
    """Ошибка bind и ранняя остановка не открывают пустую страницу."""
    calls = []
    monkeypatch.setattr(browser_launch, "open_browser", calls.append)
    browser_launch.open_when_ready(SimpleNamespace(started=False, should_exit=True), Event(), "unused")
    stopped = Event()
    stopped.set()
    browser_launch.open_when_ready(SimpleNamespace(started=True, should_exit=False), stopped, "unused")
    assert calls == []


def test_waits_for_readiness(monkeypatch):
    """Медленный старт ожидается без фиксированной задержки открытия."""
    server = SimpleNamespace(started=False, should_exit=False)
    calls = []
    monkeypatch.setattr(browser_launch, "open_browser", calls.append)
    class ReadinessEvent:
        def is_set(self):
            return False
        def wait(self, timeout):
            assert calls == []
            server.started = True
    browser_launch.open_when_ready(server, ReadinessEvent(), "ready")
    assert calls == ["ready"]


def test_cli_opens_only_when_requested_and_supports_headless(monkeypatch, tmp_path):
    """CLI запускает сервер; только явный --open дополнительно открывает браузер."""
    import sys
    import uvicorn
    calls = []
    class FakeThread:
        def __init__(self, **options):
            self.options = options
        def start(self):
            calls.append(self.options['args'][2])
    class FakeServer:
        def __init__(self, config):
            self.config = config
        def run(self):
            calls.append('server')
    monkeypatch.setattr(cli.threading, 'Thread', FakeThread)
    monkeypatch.setattr(uvicorn, 'Server', FakeServer)
    for flags, expected in [([], ['server']), (['--open'], ['http://127.0.0.1:9876', 'server']), (['--no-open'], ['server'])]:
        calls.clear()
        monkeypatch.setattr(sys, 'argv', ['atlas', '--data-dir', str(tmp_path), '--port', '9876', *flags])
        cli.main()
        assert calls == expected


def test_macos_uses_system_browser_without_applescript(monkeypatch):
    """URL передаётся отдельным аргументом системному launcher без shell."""
    calls = []
    monkeypatch.setattr(browser_launch.sys, 'platform', 'darwin')
    monkeypatch.setattr(browser_launch.subprocess, 'run', lambda args, **kw: calls.append((args, kw)))
    browser_launch.open_browser('http://127.0.0.1:8765')
    assert calls[0][0] == ['/usr/bin/open', 'http://127.0.0.1:8765']
    assert calls[0][1]['timeout'] == 10
