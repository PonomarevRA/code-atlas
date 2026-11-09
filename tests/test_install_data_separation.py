"""Совместимое обновление меняет binaries, не архивируя и не переписывая данные."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest
from native_installer_fixture import prepare_installer

INSTALLERS = Path(__file__).resolve().parents[1] / 'packaging'


@pytest.mark.parametrize('filename', ['install-macos.sh', 'install-linux.sh', 'install-windows.ps1'])
def test_installers_use_compatibility_preflight_instead_of_data_backup(filename):
    """Каждая ОС проверяет lock/schema до переименования приложения; обычный ZIP исключён."""
    text = (INSTALLERS / filename).read_text()
    assert '--prepare-update' in text
    assert '--backup-data' not in text
    replacement = 'Move-Item $Destination $Backup' if filename.endswith('.ps1') else 'mv "$DESTINATION" "$BACKUP"'
    assert text.index('--prepare-update') < text.index(replacement)
    assert 'ATLAS_DATA_DIR' in text


def _fingerprints(directory):
    """Обнаруживает изменение bytes, inode или mtime любого пользовательского файла."""
    result = {}
    for path in directory.rglob('*'):
        metadata = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        result[str(path.relative_to(directory))] = (metadata.st_ino, metadata.st_mtime_ns, metadata.st_size, digest)
    return result


@pytest.mark.skipif(sys.platform != 'darwin', reason='Uses native macOS ditto in temporary directories')
def test_update_never_copies_or_rewrites_user_documents(tmp_path):
    """Реальный installer с mock executable оставляет большой отдельный data tree неизменным."""
    source = tmp_path / 'new.app'
    executable = source / 'Contents/MacOS/CodeAtlas'
    executable.parent.mkdir(parents=True)
    call_log = tmp_path / 'calls.txt'
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$ATLAS_INSTALL_CALLS"\nexit 0\n')
    executable.chmod(0o755)
    data = tmp_path / 'separate-data'
    (data / 'document-maps/blobs').mkdir(parents=True)
    (data / 'document-maps/blobs/immutable').write_bytes(b'captured-data' * 100_000)
    (data / 'data-schema.json').write_text('{"schemaVersion":1}')
    before = _fingerprints(data)
    destination = tmp_path / 'installed.app'
    script, signing_environment = prepare_installer(tmp_path, INSTALLERS / 'install-macos.sh', [source])
    result = subprocess.run(['sh', str(script), str(source), str(destination)],
                            env={**os.environ, **signing_environment, 'ATLAS_DATA_DIR': str(data), 'ATLAS_INSTALL_CALLS': str(call_log)},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert _fingerprints(data) == before
    assert '--prepare-update' in call_log.read_text()
    assert not list(tmp_path.rglob('*.zip'))
    assert not (destination / 'document-maps').exists()


@pytest.mark.skipif(sys.platform == 'win32', reason='Exercises POSIX shell installers')
@pytest.mark.parametrize('filename', ['install-macos.sh', 'install-linux.sh'])
def test_installer_rejects_data_embedded_inside_replaced_app(tmp_path, filename):
    """Старые данные внутри app требуют явного переноса, а не молчаливой потери при update."""
    source = tmp_path / 'new'
    relative_binary = 'Contents/MacOS/CodeAtlas' if filename == 'install-macos.sh' else 'CodeAtlas'
    binary = source / relative_binary
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    destination = tmp_path / 'installed'
    data = destination / 'data'
    data.mkdir(parents=True)
    marker = data / 'keep.txt'
    marker.write_text('preserved')
    result = subprocess.run(['sh', str(INSTALLERS / filename), str(source), str(destination)],
                            env={**os.environ, 'ATLAS_DATA_DIR': str(data)}, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'outside the application' in result.stderr
    assert marker.read_text() == 'preserved'
    assert not list(tmp_path.glob('installed.previous-*'))
