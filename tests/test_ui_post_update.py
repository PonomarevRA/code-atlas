"""Проверки UI после обновления входят в обычный pytest без дополнительного test stack."""

from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from atlas import desktop_window


BASE_URL = 'http://127.0.0.1:8765'


class PickerWindow:
    """Native fixture фиксирует только UI-вызовы; выбранные папки не читаются."""

    def __init__(self, answers, current_url=BASE_URL + '/?repo=one&node=method'):
        """Задаёт ответы picker и текущую карту без взаимодействия с macOS."""
        self.answers = list(answers)
        self.current_url = current_url
        self.picks = []
        self.loads = []
        self.errors = []

    def create_file_dialog(self, kind, **options):
        """Выдаёт управляемый выбор, отмену, ошибку или повторный вход в action."""
        self.picks.append((kind, options))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer() if callable(answer) else answer

    def get_current_url(self):
        return self.current_url

    def load_url(self, url):
        self.loads.append(url)

    def create_confirmation_dialog(self, title, message):
        self.errors.append((title, message))


def picker_action(window):
    """Создаёт production closure с уникальным marker режима выбора папки."""
    webview = SimpleNamespace(FileDialog=SimpleNamespace(FOLDER='folder-mode'))
    return desktop_window.folder_access_action(window, webview, BASE_URL)


def test_post_update_ui_contracts():
    """Проверяет масштаб снимка, timeout/retry загрузки и экспорта, восстановление поиска после Back."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    script = Path(__file__).with_suffix('.mjs')
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Post-update fitted zoom, read/export deadline recovery and search history PASS' in result.stdout


@pytest.mark.parametrize('answer', [None, (), []])
def test_folder_picker_cancel_is_noop(answer):
    """Отмена не перезагружает карту и не заменяет её выбранным путём."""
    window = PickerWindow([answer])
    picker_action(window)()
    assert window.loads == [] and window.errors == []
    assert window.picks == [('folder-mode', {'directory': '', 'allow_multiple': False})]


@pytest.mark.parametrize('current,expected', [
    (BASE_URL + '/?repo=one&node=method#reader', BASE_URL + '/?repo=one&node=method#reader'),
    (None, BASE_URL),
    ('https://example.com/?repo=other', BASE_URL),
    ('http://127.0.0.1:8766/', BASE_URL),
    ('javascript:alert(1)', BASE_URL),
])
def test_folder_picker_selected_folder_reloads_only_current_local_map(current, expected):
    """Выбранный путь не попадает в URL; origin и текущий контекст карты защищены."""
    window = PickerWindow([('/tmp/a-selected-project',)], current_url=current)
    picker_action(window)()
    assert window.loads == [expected]
    assert window.picks == [('folder-mode', {'directory': '', 'allow_multiple': False})]
    assert window.errors == []


def test_folder_picker_reentry_does_not_open_another_dialog():
    """Повторный вызов во время picker пропускается, следующий самостоятельный выбор разрешён."""
    window = PickerWindow([])
    action = picker_action(window)

    def nested_choice():
        """Моделирует второй menu callback, пока первый picker ещё открыт."""
        action()
        return ('/tmp/selected',)

    window.answers = [nested_choice, None]
    action()
    assert len(window.picks) == 1 and window.loads == [window.current_url]
    action()
    assert len(window.picks) == 2 and len(window.loads) == 1


@pytest.mark.parametrize('failure_stage', ['picker', 'reload', 'error-dialog'])
def test_folder_picker_error_reports_and_releases_lock(failure_stage, monkeypatch, caplog):
    """Ошибка UI остаётся видимой в логе и не блокирует повторную команду меню."""
    window = PickerWindow([RuntimeError('picker unavailable'), None])

    def fail(*args, **kwargs):
        """Моделирует отказ native UI без доступа к файловой системе."""
        raise RuntimeError('native UI unavailable')

    if failure_stage == 'reload':
        window.answers[0] = ('/tmp/selected',)
        monkeypatch.setattr(window, 'load_url', fail)
    elif failure_stage == 'error-dialog':
        monkeypatch.setattr(window, 'create_confirmation_dialog', fail)
    action = picker_action(window)
    action()
    assert 'Native project folder selection failed' in caplog.text
    if failure_stage == 'error-dialog':
        assert 'Native error dialog unavailable' in caplog.text
    else:
        assert len(window.errors) == 1
    action()
    assert len(window.picks) == 2, 'the error must release the nonblocking picker lock'


@pytest.mark.parametrize('platform,has_picker', [('darwin', True), ('win32', False), ('linux', False)])
def test_folder_picker_menu_is_macos_only(platform, has_picker, monkeypatch, tmp_path):
    """Команда разрешения папки не добавляется в меню платформ с другим механизмом доступа."""
    menu_type = lambda title, items: SimpleNamespace(title=title, items=items)
    action_type = lambda title, function: SimpleNamespace(title=title, function=function)
    monkeypatch.setitem(sys.modules, 'webview.menu', SimpleNamespace(Menu=menu_type, MenuAction=action_type, MenuSeparator=object))
    monkeypatch.setattr(sys, 'platform', platform)
    window = PickerWindow([None])
    window.destroy = lambda: None
    class LoadedHandlers(list):
        """Retain the renderer's page-ready callback without starting Cocoa."""
        def __iadd__(self, callback):
            self.append(callback)
            return self

        def __isub__(self, callback):
            """Release callbacks during the real background handle's shutdown."""
            self.remove(callback)
            return self
    window.events = SimpleNamespace(loaded=LoadedHandlers(), shown=LoadedHandlers(), closing=LoadedHandlers())
    window.run_js = lambda script: False
    native = {}
    from atlas import native_menu, native_toolbar
    monkeypatch.setattr(native_menu, 'install_native_menu', lambda *args, **kwargs: native.update(kwargs))
    monkeypatch.setattr(native_toolbar, 'install_native_toolbar', lambda *args, **kwargs: None)
    started = {}
    webview = SimpleNamespace(settings={}, create_window=lambda *args, **kwargs: window,
                              start=lambda **kwargs: started.update(kwargs), FileDialog=SimpleNamespace(FOLDER='folder-mode'))
    desktop_window.show_window(webview, BASE_URL, tmp_path)
    if platform == 'darwin':
        for loaded in window.events.loaded:
            loaded()
        assert started['menu'] == []
        titles = [title for title, callback in native['file_actions']]
        assert 'Choose Project Folder…' in titles
    else:
        titles = [getattr(item, 'title', '') for item in started['menu'][0].items]
        assert 'Выбрать папку проекта для чтения…' not in titles
