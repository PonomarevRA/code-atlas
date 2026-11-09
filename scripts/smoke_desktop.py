"""Проверка настоящего WebView: загрузка локального Atlas и завершение без ручных кликов."""

import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas.desktop_launch import run_desktop
from atlas import desktop_window


def main() -> None:
    """В desktop сессии (Linux: xvfb-run) проверяет DOM, API и остановку сервера."""
    import uvicorn
    import webview

    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    checked = Event()
    result = {}
    original_show = desktop_window.show_window

    def show(webview_module, url, data_dir, *, on_shutdown=None):
        """Устанавливает проверку только в тестовом процессе, без API в production UI."""
        original_start = webview_module.start

        def start(**options):
            window = webview_module.windows[0]

            def inspect():
                try:
                    if not window.events.loaded.wait(30):
                        raise RuntimeError("WebView load timeout")
                    # evaluate_js использует eval, запрещённый CSP Atlas. Прямая
                    # инъекция smoke кода не ослабляет политику production страницы.
                    window.run_js("fetch('/api/health').then(r => r.json()).then(r => { window.__atlasSmokeHealth = r; });")
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        value = window.run_js("JSON.stringify({ui: {title: document.title, menu: !!document.querySelector('.atlas-menu')}, health: window.__atlasSmokeHealth})")
                        payload = json.loads(value) if isinstance(value, str) else value
                        if payload and payload["ui"]["menu"] and payload.get("health"):
                            result.update(payload)
                            break
                        checked.wait(0.1)
                    else:
                        raise RuntimeError("Atlas DOM/API readiness timeout")
                    if sys.platform == "darwin":
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            if window.run_js("document.documentElement.classList.contains('native-toolbar') && document.documentElement.classList.contains('native-project-picker')"):
                                break
                            checked.wait(0.1)
                        else:
                            raise RuntimeError("Native sidebar capability timeout")
                        result["nativeSidebar"] = json.loads(window.run_js(
                            "JSON.stringify({side: 'left', width: document.getElementById('workspace-left').getBoundingClientRect().right, "
                            "api: typeof window.pywebview.api.standalone_sidebar_changed})"))
                        assert result["nativeSidebar"]["api"] == "function"
                        assert window._standalone_sidebar_accessory.state["side"] == "left"
                        from PyObjCTools import AppHelper
                        native_checked = Event()

                        def inspect_project_button():
                            """Read actual AppKit controls and exercise the native target on Cocoa's main thread."""
                            try:
                                accessory = window._standalone_sidebar_accessory
                                picker = accessory.navigation.buttons['projects']
                                assert picker.isEnabled() and picker.toolTip() == 'Выбрать проект'
                                frames = [accessory.navigation.buttons[name].frame() for name in ('projects', 'back', 'forward')]
                                frames.append(accessory.button.frame())
                                for left, right in zip(frames, frames[1:]):
                                    assert left.origin.x + left.size.width < right.origin.x
                                picker.performClick_(None)
                                result['nativeProjectPicker'] = {'enabled': True, 'nonOverlapping': True, 'actionDispatched': True}
                            except Exception as error:
                                result['error'] = str(error)
                            finally:
                                native_checked.set()

                        # A native identity by itself must never hide the only project selector.
                        fallback = json.loads(window.run_js("""JSON.stringify((() => {
                            const root=document.documentElement,brand=document.querySelector('.workspace-brand');
                            const installed=getComputedStyle(brand).display==='none';
                            root.classList.remove('native-project-picker');
                            const withoutPicker=getComputedStyle(brand).display!=='none';
                            root.classList.add('native-project-picker');root.classList.remove('native-toolbar');
                            const withoutToolbar=getComputedStyle(brand).display!=='none';
                            root.classList.add('native-toolbar');
                            return {installed,withoutPicker,withoutToolbar};
                        })())"""))
                        assert all(fallback.values()), fallback
                        window.run_js("document.getElementById('panel-left-toggle').click()")
                        deadline = time.monotonic() + 10
                        while not window._standalone_sidebar_accessory.state['collapsed']:
                            if time.monotonic() >= deadline:
                                raise RuntimeError('Native toolbar did not follow the collapsed sidebar')
                            checked.wait(.1)
                        AppHelper.callAfter(inspect_project_button)
                        assert native_checked.wait(5), 'Native project button did not respond'
                        assert 'error' not in result, result
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            if window.run_js("location.pathname==='/' && location.search==='' && !!document.getElementById('projects')"):
                                result['nativeProjectPicker']['selectionPageOpened'] = True
                                break
                            checked.wait(.1)
                        else:
                            raise RuntimeError('Native project button did not open project selection')
                        result['projectPickerFallback'] = fallback
                        result['nativeProjectPicker']['collapsedSidebar'] = True
                    tray = window._standalone_background
                    deadline = time.monotonic() + (10 if sys.platform == "darwin" else 1)
                    while not tray.ready and time.monotonic() < deadline:
                        checked.wait(.05)
                    if not tray.ready and sys.platform == "darwin":
                        raise RuntimeError("Native tray registration failed")
                    if tray.ready:
                        if sys.platform == "darwin":
                            from PyObjCTools import AppHelper
                            AppHelper.callAfter(window.native.performClose_, None)
                        else:
                            window.destroy()
                        checked.wait(.3)
                        assert tray.ready and not tray.closed, "Normal close destroyed the tray"
                        import urllib.request
                        with urllib.request.urlopen(url + "/api/desktop/v1/widget", timeout=3) as response:
                            widget = json.load(response)
                        assert widget["appId"] == "code-atlas"
                        window.show()
                        result["tray"] = {"ready": True, "closeKeptServer": True, "sameOwner": True}
                    else:
                        result["tray"] = {"ready": False, "unsupportedDesktopFallback": True}
                    result["renderer"] = webview_module.renderer
                except Exception as error:
                    result["error"] = str(error)
                finally:
                    checked.set()
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    window._standalone_request_quit()

            original_start(func=inspect, **options)

        webview_module.start = start
        try:
            original_show(webview_module, url, data_dir, on_shutdown=on_shutdown)
        finally:
            webview_module.start = original_start

    # run_desktop imports this function once; interception stays inside the smoke process.
    import atlas.desktop_launch as launch
    launch.show_window = show
    with tempfile.TemporaryDirectory(prefix="atlas-desktop-smoke-") as temporary:
        os.environ["ATLAS_DATA_DIR"] = temporary
        server = uvicorn.Server(uvicorn.Config("atlas.server:create_app", factory=True, host="127.0.0.1", port=port,
                                               timeout_graceful_shutdown=10, log_level="warning"))
        run_desktop(server, f"http://127.0.0.1:{port}", Path(temporary))
    assert checked.is_set() and "error" not in result, result
    assert result["ui"]["menu"] and result["health"]["status"] == "ok", result
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    if "--child" in sys.argv:
        main()
    else:
        import subprocess
        completed = subprocess.run([sys.executable, "-u", __file__, "--child"], capture_output=True, text=True, timeout=100)
        records = [line for line in completed.stdout.splitlines() if line.startswith('{"ui":') or line.startswith('{"error":')]
        assert completed.returncode == 0 and records, (completed.returncode, completed.stderr, completed.stdout)
        receipt = json.loads(records[-1])
        assert "error" not in receipt, receipt
        if receipt["tray"]["ready"]:
            assert receipt["tray"]["closeKeptServer"] and receipt["tray"]["sameOwner"], receipt
        else:
            assert sys.platform != "darwin" and receipt["tray"]["unsupportedDesktopFallback"], receipt
        print(json.dumps(receipt, ensure_ascii=False))
