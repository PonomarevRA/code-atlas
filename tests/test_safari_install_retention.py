"""Verify quiet Safari install transactions with isolated synthetic bundles and injected OS boundaries."""
from pathlib import Path
import plistlib
import shutil

import pytest

from desktop_support.obsolete_apps import CleanupRefused
from tools.install_safari_browser import EXTENSION, install


def app(path, version):
    """Create an inert containing app/extension tree with no real data or executable code."""
    (path / 'Contents/MacOS').mkdir(parents=True)
    (path / 'Contents/MacOS/Test').write_bytes(version)
    (path / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.code-atlas.browser', 'CFBundleExecutable': 'Test', 'CFBundlePackageType': 'APPL'}))
    (path / EXTENSION / 'Contents').mkdir(parents=True)
    (path / EXTENSION / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.code-atlas.browser.Extension'}))
    return path


def native(path):
    """Read only the synthetic version marker used to distinguish current and staged bundles."""
    return (path / 'Contents/MacOS/Test').read_bytes()


def run(source, destination, **overrides):
    """Use real filesystem transitions while isolating signatures, registration and live OS owners."""
    boundaries = {'qualify': lambda path: None, 'register': lambda path: None, 'owners': lambda path: [], 'copy': lambda a, b: shutil.copytree(a, b, symlinks=True)}
    boundaries.update(overrides)
    return install(source, destination, **boundaries)


def test_verified_success_is_quiet_and_retires_transaction_backup(tmp_path):
    """Fresh install and update leave only current app plus the caller-owned distribution artifact."""
    source = app(tmp_path / 'Source.app', b'new')
    target = tmp_path / 'Current.app'
    registered = []
    result = run(source, target, register=lambda path: registered.append(path))
    assert native(target) == b'new' and native(source) == b'new'
    assert result['browserLaunched'] is False and result['previousRetained'] is None
    (target / 'Contents/MacOS/Test').write_bytes(b'old')
    store = tmp_path / 'external-data'; store.mkdir(); (store / 'keep.db').write_bytes(b'keep')
    result = run(source, target, register=lambda path: registered.append(path))
    assert result['previousRetained'] is None and native(target) == b'new'
    assert not list(tmp_path.glob('Atlas Browser.previous-*.app'))
    assert registered == [target, target] and (store / 'keep.db').read_bytes() == b'keep'


@pytest.mark.parametrize('boundary', ['signature', 'registration'])
def test_publish_failure_restores_old_app(tmp_path, boundary):
    """A failed relocated verification or first registration restores the original complete bundle."""
    source = app(tmp_path / 'Source.app', b'new'); target = app(tmp_path / 'Current.app', b'old')
    def qualify(path):
        """Reject only the new canonical app; restoration retains valid old code."""
        if boundary == 'signature' and path == target and native(path) == b'new': raise RuntimeError('signature failure')
    def register(path):
        """Model one failed new registration and a successful rollback registration."""
        if boundary == 'registration' and native(path) == b'new': raise RuntimeError('registration failure')
    with pytest.raises(RuntimeError, match='failure'):
        run(source, target, qualify=qualify, register=register)
    assert native(target) == b'old' and not list(tmp_path.glob('Atlas Browser.previous-*.app'))
    assert not list(tmp_path.glob('.atlas-browser-install-*.app'))


def test_late_resource_owner_prevents_replacement(tmp_path):
    """An owner appearing during staging must prevent the first rename."""
    source = app(tmp_path / 'Source.app', b'new'); target = app(tmp_path / 'Current.app', b'old')
    calls = 0
    def owners(path):
        """Acquire the target on the second check, after copying and verification."""
        nonlocal calls
        calls += 1
        return [123] if path == target and calls >= 2 else []
    with pytest.raises(CleanupRefused, match='became active'):
        run(source, target, owners=owners)
    assert native(target) == b'old' and not list(tmp_path.glob('Atlas Browser.previous-*.app'))


def test_old_copy_acquired_after_publish_is_retained(tmp_path):
    """A valid installed app remains installed when an old resource prevents retirement."""
    source = app(tmp_path / 'Source.app', b'new'); target = app(tmp_path / 'Current.app', b'old')
    result = run(source, target, owners=lambda path: [123] if '.previous-' in path.name else [])
    assert result['previousRetained'] and native(target) == b'new'
    assert native(Path(result['previousRetained'])) == b'old'


def test_protected_configuration_or_foreign_target_is_unchanged(tmp_path):
    """Installer refuses durable data in app trees and cannot replace another product."""
    source = app(tmp_path / 'Source.app', b'new'); target = app(tmp_path / 'Current.app', b'old')
    protected = target / 'Contents/APPSETTINGS.LOCAL.JSON'; protected.write_bytes(b'synthetic')
    with pytest.raises(CleanupRefused, match='embedded-data'):
        run(source, target)
    assert native(target) == b'old' and protected.read_bytes() == b'synthetic'
    protected.unlink()
    info = target / 'Contents/Info.plist'; value = plistlib.loads(info.read_bytes()); value['CFBundleIdentifier'] = 'foreign.product'; info.write_bytes(plistlib.dumps(value))
    with pytest.raises(CleanupRefused, match='Foreign'):
        run(source, target)
    assert native(target) == b'old'


def test_active_new_app_is_preserved_if_registration_fails(tmp_path):
    """Failure recovery must not delete a new bundle acquired by a concurrent external process."""
    source = app(tmp_path / 'Source.app', b'new'); target = app(tmp_path / 'Current.app', b'old')
    active = False
    def register(path):
        """Model a process starting the newly published app while registration fails."""
        nonlocal active
        active = True
        raise RuntimeError('registration failure')
    with pytest.raises(CleanupRefused, match='both it and the rollback are retained'):
        run(source, target, owners=lambda path: [123] if active else [], register=register)
    assert native(target) == b'new'
    assert len(list(tmp_path.glob('Atlas Browser.previous-*.app'))) == 1


def test_malformed_payload_and_non_app_destination_are_refused(tmp_path):
    """A signed source boundary cannot turn mismatched extension metadata into a Safari app."""
    source = app(tmp_path / 'Source.app', b'new')
    with pytest.raises(CleanupRefused, match='application bundle'):
        run(source, tmp_path / 'not-an-app')
    info = source / EXTENSION / 'Contents/Info.plist'
    info.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'foreign.extension'}))
    with pytest.raises(CleanupRefused, match='bundle metadata'):
        run(source, tmp_path / 'Current.app')
    assert not (tmp_path / 'Current.app').exists()


def test_failed_staging_with_new_data_and_owner_is_retained(tmp_path):
    """Failed verification cannot authorize deleting an acquired staging bundle or its new data."""
    source = app(tmp_path / 'Source.app', b'new'); target = app(tmp_path / 'Current.app', b'old')
    def qualify(path):
        """Model another process acquiring the stage and writing data while verification fails."""
        if path.name.startswith('.atlas-browser-install-'):
            (path / 'Contents/new-data.db').write_bytes(b'unique-synthetic')
            raise RuntimeError('verification failed')
    with pytest.raises(CleanupRefused, match='staging retained'):
        run(source, target, qualify=qualify, owners=lambda path: [123] if path.name.startswith('.atlas-browser-install-') else [])
    stages = list(tmp_path.glob('.atlas-browser-install-*.app'))
    assert len(stages) == 1 and (stages[0] / 'Contents/new-data.db').read_bytes() == b'unique-synthetic'
    assert native(target) == b'old'


def test_changed_old_rollback_is_not_reauthorized_for_deletion(tmp_path):
    """Retirement uses the pre-update identity rather than approving changes after rename."""
    source = app(tmp_path / 'Source.app', b'new'); target = app(tmp_path / 'Current.app', b'old')
    def register(path):
        """Mutate the old copy while the new app is registered, without modifying the new app."""
        old = next(tmp_path.glob('Atlas Browser.previous-*.app'))
        (old / 'Contents/MacOS/Test').write_bytes(b'changed-by-external-owner')
    result = run(source, target, register=register)
    assert result['previousRetained'] and native(Path(result['previousRetained'])) == b'changed-by-external-owner'
    assert native(target) == b'new'
