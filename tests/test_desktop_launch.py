"""Desktop lifecycle сохраняет главный GUI поток и не оставляет сервер после закрытия."""

from threading import Event, current_thread, main_thread
from types import SimpleNamespace
import sys

import pytest

class LoadedHandlers(list):
    """Match pywebview's event subscription without initializing Cocoa."""

    def __iadd__(self, handler):
        """Retain a registered callback for lifecycle assertions."""
        self.append(handler)
        return self

    def __isub__(self, handler):
        """Detach a subscription during desktop teardown."""
        self.remove(handler)
        return self


from atlas import cli, desktop_launch, desktop_window, runtime_paths


def test_desktop_waits_for_startup_and_stops_owned_server(monkeypatch, tmp_path):
    """Окно открывается после готовности; GUI завершается раньше освобождения сервера."""
    stopped = Event()

    class Server:
        config = SimpleNamespace()
        started = False
        should_exit = False

        def run(self):
            self.started = True
            while not self.should_exit:
                stopped.wait(0.005)
            stopped.set()

    server = Server()
    monkeypatch.setattr(desktop_launch, "load_webview", lambda: "webview")

    def show(webview, url, data_dir, *, on_shutdown):
        assert current_thread() is main_thread()
        assert server.started and not stopped.is_set()
        assert (webview, url, data_dir) == ("webview", "http://127.0.0.1:9876", tmp_path)

    monkeypatch.setattr(desktop_launch, "show_window", show)
    desktop_launch.run_desktop(server, "http://127.0.0.1:9876", tmp_path)
    assert stopped.is_set() and server.should_exit


def test_bind_failure_never_opens_window(monkeypatch, tmp_path):
    """SystemExit в Uvicorn thread становится понятной ошибкой desktop startup."""
    server = SimpleNamespace(started=False, should_exit=False, config=SimpleNamespace())

    def failed_start():
        raise SystemExit("address already in use")

    server.run = failed_start
    monkeypatch.setattr(desktop_launch, "load_webview", lambda: "webview")
    monkeypatch.setattr(desktop_launch, "show_window", lambda *args: pytest.fail("unexpected window"))
    with pytest.raises(RuntimeError, match="address already in use"):
        desktop_launch.run_desktop(server, "unused", tmp_path)
    assert server.should_exit


def test_gui_failure_stops_server(monkeypatch, tmp_path):
    """Отсутствующий renderer не оставляет каталог данных заблокированным сервером."""
    stopped = Event()
    server = SimpleNamespace(started=False, should_exit=False, config=SimpleNamespace())

    def serve():
        server.started = True
        while not server.should_exit:
            stopped.wait(0.005)
        stopped.set()

    def broken_window(*args, **kwargs):
        raise RuntimeError("renderer unavailable")

    server.run = serve
    monkeypatch.setattr(desktop_launch, "load_webview", lambda: "webview")
    monkeypatch.setattr(desktop_launch, "show_window", broken_window)
    with pytest.raises(RuntimeError, match="renderer unavailable"):
        desktop_launch.run_desktop(server, "unused", tmp_path)
    assert stopped.is_set()


def test_startup_timeout_and_finished_server_are_rejected():
    """Deadline не зависит от фиксированной задержки загрузки UI."""
    server = SimpleNamespace(started=False, should_exit=False, config=SimpleNamespace())
    with pytest.raises(RuntimeError, match="не запустился"):
        desktop_launch.wait_for_server(server, Event(), [], timeout=0)
    finished = Event()
    finished.set()
    server.started = True
    with pytest.raises(RuntimeError, match="до открытия окна"):
        desktop_launch.wait_for_server(server, finished, [])


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8765/?repo=abc&detail=42", "http://127.0.0.1:8765/?repo=abc&detail=42"),
    (None, "http://127.0.0.1:8765"),
    ("https://example.com", "http://127.0.0.1:8765"),
    ("http://127.0.0.1:8766", "http://127.0.0.1:8765"),
    ("javascript:alert(1)", "http://127.0.0.1:8765"),
])
def test_browser_menu_preserves_local_map_only(url, expected):
    """Команда открытия браузера сохраняет карту и не принимает чужой origin."""
    window = SimpleNamespace(get_current_url=lambda: url)
    assert desktop_window.current_local_url(window, "http://127.0.0.1:8765") == expected


def test_cli_desktop_does_not_open_browser(monkeypatch, tmp_path):
    """Desktop и browser являются отдельными взаимоисключающими способами запуска."""
    import uvicorn
    from atlas import server as server_module

    calls = []
    stop = lambda: None
    app = SimpleNamespace(state=SimpleNamespace(request_shutdown=stop))
    monkeypatch.setattr(server_module, "create_app", lambda: app)

    def run_desktop(server, url, data, *, request_shutdown):
        """Verify the application callback is passed explicitly, without Uvicorn internals."""
        assert server.config.app is app and not server.config.factory
        assert request_shutdown is stop
        calls.append((url, data))

    monkeypatch.setattr(desktop_launch, "run_desktop", run_desktop)
    monkeypatch.setattr(cli.threading, "Thread", lambda **kwargs: pytest.fail("browser thread started"))
    monkeypatch.setattr(uvicorn, "Server", lambda config: SimpleNamespace(config=config))
    monkeypatch.setattr(sys, "argv", ["atlas", "--desktop", "--port", "9876", "--data-dir", str(tmp_path)])
    cli.main()
    assert calls == [("http://127.0.0.1:9876", tmp_path)]
    for other_mode in ("--open", "--no-open"):
        monkeypatch.setattr(sys, "argv", ["atlas", "--desktop", other_mode])
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2


def test_desktop_signals_application_before_server_starts_draining(monkeypatch, tmp_path):
    """The stop callback can release an admitted operation before Uvicorn observes should_exit."""
    from atlas.http_lifecycle import HTTPState

    stopped, active_request = Event(), Event()
    signals = []
    server = SimpleNamespace(started=False, should_exit=False, config=SimpleNamespace())

    def signal():
        """An explicit nonblocking cancellation signal arrives before request draining starts."""
        assert not server.should_exit
        signals.append("stop")
        active_request.set()

    state = HTTPState((signal,))

    def serve():
        """Represent an owned server that needs cooperative cancellation before it can finish."""
        server.started = True
        assert active_request.wait(5)
        while not server.should_exit:
            stopped.wait(.001)
        stopped.set()

    def show(webview, url, data_dir, *, on_shutdown):
        """A normal close invokes the same callback subsequently used by finally cleanup."""
        assert on_shutdown()

    server.run = serve
    monkeypatch.setattr(desktop_launch, "load_webview", lambda: "webview")
    monkeypatch.setattr(desktop_launch, "show_window", show)
    desktop_launch.run_desktop(server, "unused", tmp_path, request_shutdown=state.request_stop)
    assert stopped.is_set() and signals == ["stop"]
    assert server.config.timeout_graceful_shutdown == 2


def test_cli_browser_mode_retains_factory_and_no_desktop_callback(monkeypatch, tmp_path):
    """Headless startup keeps Uvicorn's app factory and does not initialize a native window."""
    import uvicorn

    configs = []

    def server(config):
        """Record the existing browser launch contract without starting an HTTP listener."""
        configs.append(config)
        return SimpleNamespace(run=lambda: None)

    monkeypatch.setattr(uvicorn, "Server", server)
    monkeypatch.setattr(desktop_launch, "run_desktop", lambda *args, **kwargs: pytest.fail("desktop started"))
    monkeypatch.setattr(sys, "argv", ["atlas", "--no-open", "--data-dir", str(tmp_path)])
    cli.main()
    assert len(configs) == 1
    assert configs[0].app == "atlas.server:create_app" and configs[0].factory
    assert configs[0].timeout_graceful_shutdown == 10


def test_windowed_runtime_exports_console_executable(monkeypatch, tmp_path):
    """MCP и worker командная строка Windows не получают GUI executable без stdio."""
    from atlas.agent_setup import agent_mcp_config

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "CodeAtlasDesktop.exe"))
    console = str(tmp_path / "CodeAtlas.exe")
    assert runtime_paths.worker_executable() == console
    assert agent_mcp_config(9876)["mcpServers"]["code-atlas-agent"]["command"] == console
    monkeypatch.setattr(sys, "executable", str(tmp_path / "CodeAtlas"))
    assert runtime_paths.worker_executable() == sys.executable
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "CodeAtlasDesktop.exe"))
    assert runtime_paths.worker_executable() == sys.executable


def test_windows_workers_hide_console_without_changing_stdio(monkeypatch):
    """Платформенный флаг применяется только в Windows; POSIX не получает Windows flags."""
    monkeypatch.setattr(runtime_paths.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert runtime_paths.worker_creation_flags() == 0x08000000
    monkeypatch.setattr(sys, "platform", "darwin")
    assert runtime_paths.worker_creation_flags() == 0


@pytest.mark.parametrize("platform,renderer", [("darwin", "cocoa"), ("win32", "edgechromium"), ("linux", "qt")])
def test_desktop_window_menu_storage_and_exports(monkeypatch, tmp_path, platform, renderer):
    """Одинаковая оболочка сохраняет URL карты, downloads и локальный профиль на каждой ОС."""
    class Menu:
        def __init__(self, title, items):
            self.title, self.items = title, items

    class Action:
        def __init__(self, title, function):
            self.title, self.function = title, function

    monkeypatch.setitem(sys.modules, "webview.menu", SimpleNamespace(Menu=Menu, MenuAction=Action, MenuSeparator=object))
    monkeypatch.setattr(sys, "platform", platform)
    current_url = "http://127.0.0.1:9876/?repo=abc&detail=42"
    calls = []
    window = SimpleNamespace(get_current_url=lambda: current_url, load_url=lambda url: calls.append(url),
                             destroy=lambda: calls.append("close"), events=SimpleNamespace(loaded=LoadedHandlers(), shown=LoadedHandlers()))

    def create(title, url, **options):
        assert title == "Code Atlas" and url == "http://127.0.0.1:9876?desktop=1"
        assert options["text_select"] and options["min_size"] == (960, 640)
        assert "js_api" not in options
        return window

    options = {}
    webview = SimpleNamespace(settings={}, create_window=create, start=lambda **kwargs: options.update(kwargs))
    monkeypatch.setattr(desktop_window, "open_browser", calls.append)
    desktop_window.show_window(webview, "http://127.0.0.1:9876", tmp_path)
    assert options["gui"] == renderer and options["private_mode"] is False
    assert options["storage_path"] == str(tmp_path / "desktop-webview")
    assert webview.settings == {"ALLOW_DOWNLOADS": True, "OPEN_EXTERNAL_LINKS_IN_BROWSER": True}
    if platform == "darwin":
        assert options["menu"] == [] and window.events.loaded
        return
    options["menu"][0].items[0].function()
    assert calls == [current_url]
    options["menu"][0].items[-1].function()
    assert calls[-1] == "close"


def test_native_url_preserves_route_query_fragment():
    """Native framing marks navigation without discarding the user's selected map."""
    assert desktop_window.desktop_url("http://127.0.0.1:8765/wiki.html?repo=a&desktop=0#x") == "http://127.0.0.1:8765/wiki.html?repo=a&desktop=1#x"
