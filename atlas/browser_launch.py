"""Открытие локального интерфейса только после успешного старта HTTP сервера."""

import webbrowser
import subprocess
import sys


def open_when_ready(server, stopped, url: str) -> None:
    """Ждёт готовность Uvicorn; при ошибке запуска или остановке вкладка не создаётся."""
    while not stopped.is_set():
        if server.started:
            open_browser(url)
            return
        if server.should_exit:
            return
        stopped.wait(0.1)


def open_browser(url: str) -> None:
    """На macOS передаёт URL LaunchServices без AppleScript; иначе использует webbrowser."""
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", url], check=False, timeout=10)
    else:
        webbrowser.open(url)
