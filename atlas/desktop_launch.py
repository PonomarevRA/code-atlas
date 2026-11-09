"""Жизненный цикл локального сервера при работе в отдельном desktop окне."""

from pathlib import Path
import threading
import time

from .native_shutdown import SERVER_DRAIN_SECONDS

from .desktop_window import load_webview, show_window


def wait_for_server(server, finished: threading.Event, failures: list, timeout: float = 60) -> None:
    """Не открывает пустое окно при отказе bind, ошибке startup или истечении deadline."""
    deadline = time.monotonic() + timeout
    while not server.started:
        if finished.is_set() or server.should_exit:
            message = str(failures[0]) if failures else "HTTP сервер остановился при запуске"
            raise RuntimeError("Не удалось запустить Atlas: " + message)
        if time.monotonic() >= deadline:
            raise RuntimeError("HTTP сервер Atlas не запустился за 60 секунд")
        finished.wait(0.05)
    if finished.is_set():
        raise RuntimeError("HTTP сервер Atlas завершился до открытия окна")


def run_desktop(server, url: str, data_dir: Path, *, request_shutdown=None, automation=None) -> None:
    """Run GUI on the main thread; signal application cancellation before bounded HTTP draining."""
    webview = load_webview()
    server.config.timeout_graceful_shutdown = 2
    finished = threading.Event()
    failures = []

    def serve():
        """Сохраняет startup failure, включая SystemExit Uvicorn при занятом порте."""
        try:
            server.run()
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=serve, name="atlas-http", daemon=True)
    worker.start()

    def shutdown_owned_server():
        """Signal workers without closing resources; lifespan releases them after HTTP draining."""
        try:
            if request_shutdown is not None:
                request_shutdown()
        finally:
            server.should_exit = True
        worker.join(SERVER_DRAIN_SECONDS)
        return not worker.is_alive()

    try:
        wait_for_server(server, finished, failures)
        options = {"automation": automation} if automation is not None else {}
        show_window(webview, url, data_dir, on_shutdown=shutdown_owned_server, **options)
    finally:
        if not shutdown_owned_server():
            raise RuntimeError("Сервер Atlas не завершился после закрытия окна")
