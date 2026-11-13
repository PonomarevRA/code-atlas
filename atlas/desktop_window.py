"""Отдельное окно Atlas использует тот же локальный HTTP UI, что и браузер."""

from pathlib import Path
import logging
import sys
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .browser_launch import open_browser
from .runtime_paths import resource_root


def load_webview():
    """Загружает необязательные GUI зависимости только для desktop режима."""
    try:
        import webview
    except ImportError as error:
        raise RuntimeError(
            "Для отдельного окна установите packaging/requirements-desktop.txt. "
            "Режим браузера доступен через --open."
        ) from error
    return webview


def current_local_url(window, base_url: str) -> str:
    """Сохраняет параметры текущей карты; меню открывает только origin этого Atlas."""
    current = window.get_current_url() or base_url
    if urlsplit(current).netloc != urlsplit(base_url).netloc or urlsplit(current).scheme != "http":
        return base_url
    return current


def folder_access_action(window, webview, base_url: str):
    """Создаёт последовательный native picker; выбранный путь не меняет область сервера."""
    lock = Lock()

    def select_folder():
        """Выбор одной папки выражает user intent; только повторное чтение проверяет доступ."""
        if not lock.acquire(blocking=False):
            return
        try:
            # Не проверяем защищённый путь до NSOpenPanel: пользователь выбирает только свой проект.
            selected = window.create_file_dialog(webview.FileDialog.FOLDER, directory="", allow_multiple=False)
            if selected:
                window.load_url(current_local_url(window, base_url))
        except Exception:
            logging.getLogger(__name__).exception("Native project folder selection failed")
            try:
                window.create_confirmation_dialog(
                    "Code Atlas — выбор папки",
                    "Не удалось открыть папку проекта. Карта сохранена; повторите выбор через меню Atlas.",
                )
            except Exception:
                logging.getLogger(__name__).warning("Native error dialog unavailable")
        finally:
            lock.release()

    return select_folder


def show_window(webview, url: str, data_dir: Path, *, on_shutdown=None, automation=None) -> None:
    """Запускает GUI на главном потоке, сохраняя настройки вне обновляемого бандла."""
    from webview.menu import Menu, MenuAction, MenuSeparator

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window(
        "Code Atlas", desktop_url(url), width=1440, height=900, min_size=(960, 640),
        background_color="#f6f8f5", text_select=True,
    )

    def open_current_in_browser():
        """Передаёт браузеру именно текущую карту с её фокусом и раскрытиями."""
        open_browser(current_local_url(window, url))

    def open_agent_requests():
        """Open the existing live mini-chat, navigating home only when this page has no activity panel."""
        opened = window.run_js("(() => { const button = document.querySelector('[data-activity=toggle]'); if (!button) return false; if (button.getAttribute('aria-pressed') !== 'true') button.click(); return true; })()")
        if not opened:
            window.load_url(desktop_url(url + "/?agentActivity=1"))

    items = [
        MenuAction("Открыть текущую карту в браузере", open_current_in_browser),
        MenuSeparator(),
        MenuAction("Карты кода", lambda: window.load_url(url + "/")),
        MenuAction("Языковая документация", lambda: window.load_url(url + "/language-docs.html")),
        MenuAction("Техническая страница", lambda: window.load_url(url + "/technical.html")),
        MenuSeparator(),
        MenuAction("Закрыть", window.destroy),
    ]
    if sys.platform == "darwin":
        items.insert(1, MenuAction("Выбрать папку проекта для чтения…", folder_access_action(window, webview, url)))
    from desktop_support import bind_background
    from .runtime_paths import APP_VERSION
    from .release_updates import edition_releases_url
    restore_shutdown = None
    background = bind_background(
        window, app_id="code-atlas", title="Code Atlas", origin=url,
        on_quit=lambda: restore_shutdown.request_quit() if restore_shutdown else window.destroy(),
        open_activity=open_agent_requests,
        data_directory=data_dir, current_version=APP_VERSION, releases_index=edition_releases_url(),
    )
    window._standalone_background = background

    def open_wiki():
        """Open the local wiki in the same native window."""
        window.load_url(desktop_url(url + "/wiki.html"))

    help_actions = (
        ("Atlas Wiki", open_wiki),
        ("Check for Updates…", background.updates),
        ("GitHub Releases", background.releases),
    )
    menu = [Menu("Atlas", items), Menu("Help", [MenuAction(label, action) for label, action in help_actions])]
    presentation = None
    if sys.platform == "darwin":
        from .native_menu import install_native_menu
        from .native_toolbar import bind_sidebar_presentation
        presentation = bind_sidebar_presentation(
            window, url, sidebar_id="workspace-left", right_class="",
            collapsed_class="panels-left-collapsed", observe=True,
        )

        def toggle_sidebar():
            """Use the existing map sidebar control; native code does not implement a second panel state."""
            window.run_js("document.getElementById('panel-left-toggle')?.click()")

        def configure_menu():
            """Expose real responder menus while keeping existing local navigation and folder recovery."""
            supported = bool(window.run_js("!!document.getElementById('panel-left-toggle')"))
            sidebar_action = toggle_sidebar if supported else None
            install_native_menu(window, "Code Atlas", sidebar_action=sidebar_action, file_actions=(
                ("Open Current Map in Browser", open_current_in_browser),
                ("Choose Project Folder…", folder_access_action(window, webview, url)),
                ("Code Maps", lambda: window.load_url(desktop_url(url + "/"))),
                ("Language Documentation", lambda: window.load_url(desktop_url(url + "/language-docs.html"))),
                ("Technical Page", lambda: window.load_url(desktop_url(url + "/technical.html"))),
            ), help_actions=help_actions)
            from .native_toolbar import install_native_toolbar
            from .native_navigation import MapNavigation
            navigation = MapNavigation(window, presentation) if supported else None
            install_native_toolbar(window, sidebar_action, identity="Code Atlas", navigation=navigation)

        window.events.loaded += configure_menu
        menu = []
    renderer = {"darwin": "cocoa", "win32": "edgechromium"}.get(sys.platform, "qt")
    from .native_menu import bind_native_shutdown
    restore_shutdown = bind_native_shutdown(
        window, on_shutdown, on_closed=presentation.close if presentation else None,
        hide_on_close=background.hide_on_close,
    ) if on_shutdown else None
    window._standalone_request_quit = restore_shutdown.request_quit if restore_shutdown else window.destroy
    handler = None
    if automation is not None:
        from .desktop_automation_host import bind_window

        handler = bind_window(window, url)
        automation.attach(handler)
    try:
        webview.start(
            gui=renderer, menu=menu, private_mode=False,
            storage_path=str(data_dir / "desktop-webview"),
            icon=str(resource_root() / "packaging/assets/code-atlas.png"),
        )
    finally:
        if handler is not None:
            automation.detach(handler)
        background.close()
        if presentation:
            presentation.close()
        if restore_shutdown:
            restore_shutdown()


def desktop_url(url: str) -> str:
    """Mark only native navigation while preserving the current route, query and fragment."""
    parsed = urlsplit(url)
    parameters = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "desktop"]
    parameters.append(("desktop", "1"))
    return urlunsplit(parsed._replace(query=urlencode(parameters)))
