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
