"""Safety contracts for external storage, close-to-tray and pinned release discovery."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop_support.settings import installation_path, read_installation
from desktop_support.background import BackgroundHandle
from desktop_support.updates import version_tuple
from atlas.desktop_widget import widget_snapshot


def write_install(home, **changes):
    """Create isolated settings without inspecting any installed service state."""
    path = installation_path('code-atlas', platform='darwin', home=home)
    path.parent.mkdir(parents=True)
    value = {'schemaVersion': 'ag-desktop/install-v1', 'appId': 'code-atlas',
             'dataDirectory': str(home / 'data'), 'backupDirectory': str(home / 'backup'),
             'applicationPath': str(home / 'Atlas.app'), 'closeToTray': True}
    value.update(changes)
    path.write_text(json.dumps(value))
    return path


def test_installer_paths_are_separate_and_corruption_does_not_select_default(tmp_path):
    """A malformed locator cannot redirect a production user into an empty store."""
    home = tmp_path.resolve()
    path = write_install(home)
    result = read_installation('code-atlas', home / 'legacy', platform='darwin', home=home)
    assert result.data_directory == home / 'data'
    assert result.backup_directory == home / 'backup'
    assert result.close_to_tray and not result.launch_at_login
    path.write_text('{')
    with pytest.raises(ValueError):
        read_installation('code-atlas', home / 'legacy', platform='darwin', home=home)


@pytest.mark.parametrize('changes', [
    {'backupDirectory': 'relative'}, {'appId': 'ag-tasks'}, {'closeToTray': 'true'},
    {'preferredPort': True}, {'preferredPort': 80}, {'schemaVersion': 'future'},
])
def test_invalid_installations_are_rejected(tmp_path, changes):
    """Typed flags, product identity and port ranges must be checked before runtime storage opens."""
    write_install(tmp_path.resolve(), **changes)
    with pytest.raises(ValueError):
        read_installation('code-atlas', tmp_path / 'old', platform='darwin', home=tmp_path.resolve())


def test_nested_store_and_symbolic_link_are_rejected(tmp_path):
    """An update may never replace the directory containing its own live data."""
    home = tmp_path.resolve()
    path = write_install(home, dataDirectory=str(home / 'Atlas.app/data'))
    with pytest.raises(ValueError, match='separate'):
        read_installation('code-atlas', home / 'old', platform='darwin', home=home)
    path.unlink()
    path.symlink_to(home / 'missing.json')
    # A dangling locator is still an unsafe file, not a first installation.
    with pytest.raises(ValueError):
        read_installation('code-atlas', home / 'old', platform='darwin', home=home)


class Event:
    """Minimal event registry allows lifecycle tests without creating a real user window."""
    def __init__(self):
        self.handlers = []
    def __iadd__(self, callback):
        self.handlers.append(callback)
        return self
    def __isub__(self, callback):
        self.handlers.remove(callback)
        return self


def test_no_hidden_window_until_tray_is_ready_and_cleanup_is_idempotent(monkeypatch, tmp_path):
    """A failed tray must preserve access to Quit; repeated disposal must not raise."""
    monkeypatch.setattr('desktop_support.background.read_installation', lambda *args: SimpleNamespace(close_to_tray=True))
    hidden = []
    window = SimpleNamespace(events=SimpleNamespace(shown=Event()), hide=lambda: hidden.append(True))
    handle = BackgroundHandle(window, 'code-atlas', 'Atlas', 'http://127.0.0.1:8765', lambda: None, None, tmp_path, '0.1.0')
    assert not handle.hide_on_close() and not hidden
    handle.ready = True
    assert handle.hide_on_close() and hidden == [True]
    handle.close(); handle.close()
    assert not handle.hide_on_close() and not window.events.shown.handlers


def test_widget_is_bounded_redacts_credentials_and_reports_missing_journal():
    """Only completed event summaries leave the journal; no arbitrary request bodies are copied."""
    row = {'phase': 'finished', 'operation': 'search', 'request': {'summary': 'token=secret find'},
           'response': {'summary': 'x' * 2000}, 'durationMs': float('nan'), 'body': 'private'}
    journal = SimpleNamespace(error=None, disk=SimpleNamespace(read=lambda **kw: [row] * 32))
    value = widget_snapshot(journal, 'http://127.0.0.1:8765')
    assert len(value['requests']) == 8 and value['appId'] == 'code-atlas'
    assert 'secret' not in json.dumps(value) and 'private' not in json.dumps(value)
    assert value['requests'][0]['durationMs'] is None
    assert len(value['requests'][0]['responseSummary']) == 512
    assert widget_snapshot(SimpleNamespace(error='offline', disk=None), 'http://127.0.0.1:8765')['status'] == 'degraded'


def test_stable_versions_only():
    """Update discovery cannot accidentally compare prerelease text as a newer stable version."""
    assert version_tuple('v1.10.0') > version_tuple('1.9.9')
    with pytest.raises(ValueError):
        version_tuple('v1.10.0-preview')


def test_manual_update_worker_and_independent_release_action(monkeypatch, tmp_path):
    """Help can open the official listing immediately or wait for one explicit background check."""
    from threading import Event as Signal, current_thread, main_thread

    monkeypatch.setattr('desktop_support.background.read_installation', lambda *args: SimpleNamespace(close_to_tray=True))
    entered = Signal()
    proceed = Signal()
    opened = Signal()
    messages = []
    urls = []
    checks = []
    release = 'https://github.com/PonomarevRA/code-atlas/releases/tag/v1.2.4'
    listing = 'https://github.com/PonomarevRA/code-atlas/releases'

    def check(app_id, installed):
        """Hold the fixture request until the native entry point has returned."""
        checks.append((app_id, installed, current_thread()))
        entered.set()
        assert proceed.wait(3)
        return {'version': 'v1.2.4', 'available': True, 'url': release, 'notes': ['Open map groups faster.']}

    def display(title, message):
        """Capture the native dialog arguments and choose to open the release."""
        messages.append((title, message))
        return True

    def open_url(url):
        """Capture browser URLs without launching the actual browser."""
        urls.append(url)
        if url == release:
            opened.set()

    monkeypatch.setattr('desktop_support.background.check_release', check)
    monkeypatch.setattr('desktop_support.background.webbrowser.open', open_url)
    window = SimpleNamespace(events=SimpleNamespace(shown=Event()), create_confirmation_dialog=display)
    handle = BackgroundHandle(window, 'code-atlas', 'Atlas', 'http://127.0.0.1:8765', lambda: None, None, tmp_path, '1.2.3')
    try:
        handle.releases()
        assert urls == [listing] and not checks
        handle.updates()
        assert entered.wait(3)
        assert checks[0][:2] == ('code-atlas', '1.2.3') and checks[0][2] is not main_thread()
        handle.updates()
        assert len(checks) == 1 and not messages
        proceed.set()
        assert opened.wait(3)
        assert urls == [listing, release]
        title, message = messages[0]
        assert title == 'Atlas — Updates'
        assert 'Installed version: 1.2.3' in message and 'Latest release: v1.2.4' in message
        assert 'Open map groups faster.' in message and 'A native update is available.' in message
    finally:
        proceed.set()
        handle.close()


def test_manual_update_handles_unavailable_platform_and_offline_listing(monkeypatch, tmp_path):
    """The dialog distinguishes platform availability and retains a release link offline."""
    monkeypatch.setattr('desktop_support.background.read_installation', lambda *args: SimpleNamespace(close_to_tray=True))
    messages = []
    urls = []

    def display(_title, message):
        """Confirm the official fallback after capturing native copy."""
        messages.append(message)
        return True

    def offline(*_args):
        """Simulate unavailable GitHub metadata."""
        raise OSError('offline fixture')

    monkeypatch.setattr('desktop_support.background.check_release', offline)
    monkeypatch.setattr('desktop_support.background.webbrowser.open', urls.append)
    window = SimpleNamespace(events=SimpleNamespace(shown=Event()), create_confirmation_dialog=display)
    handle = BackgroundHandle(window, 'code-atlas', 'Atlas', 'http://127.0.0.1:8765', lambda: None, None, tmp_path, '1.2.3')
    assert 'not available for this platform' in handle._update_message({'version': '1.2.4', 'available': False})
    assert 'up to date' in handle._update_message({'version': '1.2.3', 'available': False})
    assert handle.update_lock.acquire(blocking=False)
    handle._check_updates()
    assert messages and 'Installed version: 1.2.3' in messages[0]
    assert urls == ['https://github.com/PonomarevRA/code-atlas/releases']
    assert not handle.update_lock.locked()
    handle.close()


def test_private_manual_update_describes_public_feed_and_opens_private_listing(monkeypatch, tmp_path):
    """Private editions use public notes as context and open their own official release index."""
    monkeypatch.setattr('desktop_support.background.read_installation', lambda *args: SimpleNamespace(close_to_tray=True))
    messages, urls = [], []
    private_listing = 'https://github.com/PonomarevRA/code-atlas-private/releases'

    def display(_title, message):
        """Capture the private update dialog and approve its link."""
        messages.append(message)
        return True

    monkeypatch.setattr('desktop_support.background.check_release', lambda *_args: {
        'version': 'v1.2.4', 'available': True, 'url': 'https://github.com/PonomarevRA/code-atlas/releases/tag/v1.2.4',
        'notes': ['Open map groups faster.'],
    })
    monkeypatch.setattr('desktop_support.background.webbrowser.open', urls.append)
    window = SimpleNamespace(events=SimpleNamespace(shown=Event()), create_confirmation_dialog=display)
    handle = BackgroundHandle(window, 'code-atlas', 'Atlas', 'http://127.0.0.1:8765', lambda: None, None, tmp_path,
                              '1.2.3', private_listing)
    handle.releases()
    assert handle.update_lock.acquire(blocking=False)
    handle._check_updates()
    assert 'Latest public release: v1.2.4' in messages[0] and 'Open map groups faster.' in messages[0]
    assert 'A native update is available' not in messages[0]
    assert urls == [private_listing, private_listing]
    handle.close()
