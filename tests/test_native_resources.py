"""Certified pack bytes survive PyInstaller rewriting and split macOS resources."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('atlas_native_resources', ROOT / 'packaging/native_resources.py')
RESOURCES = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(ROOT / 'packaging'))
try:
    SPEC.loader.exec_module(RESOURCES)
finally:
    sys.path.pop(0)


def source_pack(tmp_path):
    """Prepare distinct bytes and executable mode whose preservation is observable."""
    source = tmp_path / 'resources/lnav/darwin-x86_64'
    (source / 'lib').mkdir(parents=True)
    (source / 'licenses').mkdir()
    (source / 'lnav').write_bytes(b'certified-executable')
    (source / 'lnav').chmod(0o755)
    (source / 'lib/native.dylib').write_bytes(b'certified-library')
    (source / 'manifest.json').write_bytes(b'certified-manifest')
    (source / 'licenses/COPYING').write_bytes(b'license')
    return source


def test_restore_regular_exact_closure_in_onedir_and_split_app(tmp_path, monkeypatch):
    """Replace transformed files/symlinks and sign only the outer application."""
    from atlas import lnav_runtime
    source = source_pack(tmp_path)
    onedir = tmp_path / 'dist/CodeAtlas/_internal/resources/lnav/darwin-x86_64'
    onedir.mkdir(parents=True)
    (onedir / 'lnav').write_bytes(b'PyInstaller-rewritten-bytes')
    application = tmp_path / 'dist/Code Atlas.app'
    framework = application / 'Contents/Frameworks/resources/lnav/darwin-x86_64'
    framework.mkdir(parents=True)
    resources = application / 'Contents/Resources/resources/lnav/darwin-x86_64'
    resources.mkdir(parents=True)
    (resources / 'manifest.json').write_text('split data')
    (framework / 'manifest.json').symlink_to(resources / 'manifest.json')
    (framework / 'licenses').symlink_to(source / 'licenses')
    checked, commands = [], []

    def capability(directory):
        """The build must validate source first and each copied destination afterward."""
        checked.append(directory)
        return {'available': True}

    monkeypatch.setattr(lnav_runtime, 'capability', capability)
    monkeypatch.setattr(RESOURCES.subprocess, 'run', lambda command, **kwargs: commands.append(command))
    result = RESOURCES.restore_native_resources(tmp_path, 'macos-x64')
    assert result['lnavRestored'] and checked == [source, onedir, resources, framework]
    assert framework.is_symlink() and framework.resolve() == resources.resolve()
    for destination in (onedir, resources):
        assert not any(path.is_symlink() for path in destination.rglob('*'))
        for path in source.rglob('*'):
            if path.is_file():
                copy = destination / path.relative_to(source)
                assert copy.read_bytes() == path.read_bytes()
                assert copy.stat().st_mode & 0o777 == path.stat().st_mode & 0o777
    assert '--deep' not in commands[0] and '--timestamp=none' in commands[0]
    assert '--verify' in commands[1] and '--deep' in commands[1] and '--strict' in commands[1]
    assert (resources / 'manifest.json').read_bytes() == b'certified-manifest'


def test_invalid_source_aborts_before_any_output_mutation(tmp_path, monkeypatch):
    """A failed source proof must preserve every already-built artifact byte."""
    from atlas import lnav_runtime
    source_pack(tmp_path)
    destination = tmp_path / 'dist/CodeAtlas/_internal/resources/lnav/darwin-x86_64'
    destination.mkdir(parents=True)
    old = destination / 'lnav'
    old.write_bytes(b'previous-build')
    monkeypatch.setattr(lnav_runtime, 'capability', lambda directory: {'available': False})
    with pytest.raises(ValueError, match='source pack failed'):
        RESOURCES.restore_native_resources(tmp_path, 'darwin-x86_64')
    assert old.read_bytes() == b'previous-build'
    assert not list(destination.parent.glob('*.staging'))


def test_symlinked_parent_cannot_redirect_build_repair(tmp_path, monkeypatch):
    """Only explicit managed output roots may be changed."""
    from atlas import lnav_runtime
    source_pack(tmp_path)
    outside = tmp_path / 'keep'
    outside.mkdir()
    (outside / 'notes').write_text('keep')
    directory = tmp_path / 'dist/CodeAtlas/_internal'
    directory.mkdir(parents=True)
    (directory / 'resources').symlink_to(outside)
    monkeypatch.setattr(lnav_runtime, 'capability', lambda directory: {'available': True})
    with pytest.raises(ValueError, match='parent must not be a symlink'):
        RESOURCES.restore_native_resources(tmp_path, 'darwin-x86_64')
    assert (outside / 'notes').read_text() == 'keep'


def test_reuse_exact_whole_resource_alias_without_a_leaf_self_loop(tmp_path, monkeypatch):
    """The data-only PyInstaller layout must remain coherent after pack installation."""
    from atlas import lnav_runtime
    source = source_pack(tmp_path)
    application = tmp_path / 'dist/Code Atlas.app'
    physical = application / 'Contents/Resources/resources'
    physical.mkdir(parents=True)
    frameworks = application / 'Contents/Frameworks'
    frameworks.mkdir()
    alias = frameworks / 'resources'
    alias.symlink_to('../Resources/resources', target_is_directory=True)
    keep = physical / 'keep.txt'
    keep.write_text('unrelated resource')
    monkeypatch.setattr(lnav_runtime, 'capability', lambda directory: {'available': True})
    result = RESOURCES.restore_native_resources(tmp_path, 'macos-x64', sign=False)
    assert result['lnavRestored'] and alias.is_symlink()
    pack = physical / 'lnav/darwin-x86_64'
    through_alias = alias / 'lnav/darwin-x86_64'
    assert pack.is_dir() and not pack.is_symlink()
    assert through_alias.resolve() == pack.resolve()
    assert not any(path.is_symlink() for path in pack.rglob('*'))
    assert (pack / 'lnav').read_bytes() == (source / 'lnav').read_bytes()
    assert (through_alias / 'manifest.json').read_bytes() == b'certified-manifest'
    assert keep.read_text() == 'unrelated resource'
    # Repeating the poststep must not replace the physical pack through its alias.
    assert RESOURCES.restore_native_resources(tmp_path, 'macos-x64', sign=False)['lnavRestored']
    assert (pack / 'lnav').read_bytes() == b'certified-executable'


def test_wrong_application_resource_alias_rejected_before_any_copy(tmp_path, monkeypatch):
    """An arbitrary in-dist directory is not the allowed PyInstaller resources alias."""
    from atlas import lnav_runtime
    source_pack(tmp_path)
    application = tmp_path / 'dist/Code Atlas.app'
    physical = application / 'Contents/Resources/resources'
    physical.mkdir(parents=True)
    frameworks = application / 'Contents/Frameworks'
    frameworks.mkdir()
    foreign = application / 'Contents/Resources/foreign'
    foreign.mkdir()
    keep = foreign / 'keep'
    keep.write_text('preserve')
    alias = frameworks / 'resources'
    alias.symlink_to('../Resources/foreign', target_is_directory=True)
    onedir = tmp_path / 'dist/CodeAtlas/_internal/resources/lnav/darwin-x86_64'
    onedir.mkdir(parents=True)
    old = onedir / 'lnav'
    old.write_bytes(b'preserve-old-build')
    monkeypatch.setattr(lnav_runtime, 'capability', lambda directory: {'available': True})
    with pytest.raises(ValueError, match='Unexpected application resource directory alias'):
        RESOURCES.restore_native_resources(tmp_path, 'macos-x64', sign=False)
    assert old.read_bytes() == b'preserve-old-build'
    assert keep.read_text() == 'preserve' and alias.is_symlink()
    assert not (physical / 'lnav').exists()
