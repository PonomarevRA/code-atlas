"""Destructive cleanup operates only on exact obsolete executable bundles, never stores or active apps."""
from pathlib import Path
import plistlib
import pytest
from desktop_support.obsolete_apps import CleanupRefused, bundle_identity, remove_obsolete


def bundle(path, identifier='local.code-atlas.desktop'):
    """Build a synthetic app tree with no executable code and no real user data."""
    native = path / 'Contents/MacOS'
    native.mkdir(parents=True)
    (native / 'Test').write_bytes(b'synthetic-code')
    (path / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': identifier, 'CFBundleExecutable': 'Test'}))
    return path


def setup(tmp_path):
    """Use a current app and one obsolete copy under the same approved temporary parent."""
    return bundle(tmp_path / 'Current.app'), bundle(tmp_path / 'Old.app')


def test_deletes_only_qualified_obsolete_bundle_and_keeps_external_data(tmp_path):
    """Successful retirement cannot touch the current app, a manual ZIP or an adjacent database."""
    current, old = setup(tmp_path)
    store = tmp_path / 'user-data';store.mkdir();(store / 'memory.db').write_bytes(b'accepted')
    backup = tmp_path / 'manual.zip';backup.write_bytes(b'accepted-backup')
    qualified = []
    report = remove_obsolete(bundle_identity(old), current, [tmp_path], lambda path: [], qualify=lambda path: qualified.append(path))
    assert report['deleted'] and report['uniqueReclaimedBytes'] is None
    assert qualified == [current] and current.is_dir() and not old.exists()
    assert (store / 'memory.db').read_bytes() == b'accepted' and backup.read_bytes() == b'accepted-backup'


def test_current_and_unapproved_paths_are_never_removed(tmp_path):
    """A fabricated manifest cannot turn the canonical app into an obsolete candidate."""
    current, old = setup(tmp_path)
    with pytest.raises(CleanupRefused, match='current'):
        remove_obsolete(bundle_identity(current), current, [tmp_path], lambda path: [], qualify=lambda path: None)
    with pytest.raises(CleanupRefused, match='outside'):
        remove_obsolete(bundle_identity(old), current, [tmp_path / 'other'], lambda path: [], qualify=lambda path: None)
    assert current.exists() and old.exists()


@pytest.mark.parametrize('relative', ['.atlas/record', 'Contents/appsettings.local.json', 'Contents/records.lance', 'Contents/memory.db', 'Contents/DATA-SCHEMA.JSON', 'Contents/APPSETTINGS.LOCAL.JSON', 'Contents/MEMORY.SQLITE3'])
def test_embedded_store_or_local_secrets_are_retained(tmp_path, relative):
    """Even inside a recognized old app, data/local configuration prevents automatic deletion."""
    current, old = setup(tmp_path)
    entry = old / relative;entry.parent.mkdir(parents=True, exist_ok=True);entry.write_bytes(b'private-fixture')
    with pytest.raises(CleanupRefused, match='embedded-data'):
        remove_obsolete(bundle_identity(old), current, [tmp_path], lambda path: [], qualify=lambda path: None)
    assert entry.read_bytes() == b'private-fixture'


def test_active_owner_and_late_owner_block_removal(tmp_path):
    """Owner checks run again after the potentially slow current-app qualification."""
    current, old = setup(tmp_path);identity = bundle_identity(old)
    with pytest.raises(CleanupRefused, match='active-file-owner'):
        remove_obsolete(identity, current, [tmp_path], lambda path: [123], qualify=lambda path: None)
    active = []
    def qualify(path):
        """Model a process acquiring the old copy during signature verification."""
        active.append(123)
    with pytest.raises(CleanupRefused, match='active-file-owner'):
        remove_obsolete(identity, current, [tmp_path], lambda path: active, qualify=qualify)
    assert old.exists()


def test_tampering_and_foreign_identity_are_refused(tmp_path):
    """Exact main hash/inode and fixed product identity are required, not just an app-like name."""
    current, old = setup(tmp_path);identity = bundle_identity(old)
    (old / 'Contents/MacOS/Test').write_bytes(b'changed')
    with pytest.raises(CleanupRefused, match='candidate-changed'):
        remove_obsolete(identity, current, [tmp_path], lambda path: [], qualify=lambda path: None)
    foreign = bundle(tmp_path / 'Foreign.app', 'other.product')
    with pytest.raises(CleanupRefused, match='product-identity'):
        remove_obsolete(bundle_identity(foreign), current, [tmp_path], lambda path: [], qualify=lambda path: None)
    assert foreign.exists()


def test_failed_current_qualification_and_root_alias_preserve_old_copy(tmp_path):
    """A bad current signature cannot justify deleting the only recoverable executable."""
    current, old = setup(tmp_path)
    def reject(path):
        """Model an invalid canonical code signature."""
        raise RuntimeError('invalid-signature')
    with pytest.raises(RuntimeError, match='invalid-signature'):
        remove_obsolete(bundle_identity(old), current, [tmp_path], lambda path: [], qualify=reject)
    alias = tmp_path / 'Alias.app';alias.symlink_to(old, target_is_directory=True)
    with pytest.raises(CleanupRefused, match='redirected'):
        bundle_identity(alias)
    assert old.exists()


def test_redirected_contents_are_not_read_as_bundle_identity(tmp_path):
    """A regular app root must not hide an external Contents tree behind a child link."""
    current, old = setup(tmp_path)
    external = tmp_path / 'external-contents'
    (old / 'Contents').rename(external)
    (old / 'Contents').symlink_to(external, target_is_directory=True)
    with pytest.raises(CleanupRefused, match='unsafe-info'):
        bundle_identity(old)
    assert external.exists()


def test_owner_appearing_during_final_payload_scan_blocks_deletion(tmp_path, monkeypatch):
    """The final owner check must occur after the last potentially slow tree scan."""
    import desktop_support.obsolete_apps as cleanup
    current, old = setup(tmp_path)
    identity = bundle_identity(old)
    active = []
    original = cleanup.inspect_payload
    scans = 0
    def scan(path):
        """Acquire a resource only while completing the second/final payload inspection."""
        nonlocal scans
        result = original(path)
        scans += 1
        if scans == 2:
            active.append(123)
        return result
    monkeypatch.setattr(cleanup, 'inspect_payload', scan)
    with pytest.raises(CleanupRefused, match='active-file-owner'):
        remove_obsolete(identity, current, [tmp_path], lambda path: active, qualify=lambda path: None)
    assert old.exists() and scans == 2
