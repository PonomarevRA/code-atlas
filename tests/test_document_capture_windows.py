"""Проверки Win32 захвата: mock контракт на всех ОС и реальные handles в Windows CI."""

import os
import pytest

from atlas.documents.capture import capture
from atlas.documents.capture_windows import DIRECTORY, REPARSE_POINT, secure_open
from atlas.documents.models import DocumentError


class HandleApi:
    """Запоминает handles и моделирует junction либо уход за границу корня."""

    def __init__(self, invalid_index=None, escaped_index=None):
        self.opened = []
        self.closed = []
        self.invalid_index = invalid_index
        self.escaped_index = escaped_index

    def open(self, path, directory):
        self.opened.append((str(path), directory))
        return len(self.opened)

    def attributes(self, handle):
        attributes = DIRECTORY if self.opened[handle - 1][1] else 0
        return attributes | (REPARSE_POINT if handle == self.invalid_index else 0)

    def final_path(self, handle):
        if handle == self.escaped_index:
            return r'C:\project-sibling\outside.md'
        suffix = ['', r'\nested', r'\nested\file.md'][handle - 1]
        return '\\\\?\\C:\\project' + suffix

    def close(self, handle):
        self.closed.append(handle)


def test_windows_capture_transfers_only_final_handle(tmp_path):
    """Родители закреплены до transfer; конечный handle закрывает только CRT владелец."""
    api = HandleApi()
    transferred = []
    descriptor = secure_open(tmp_path, 'nested/file.md', api=api,
                             to_descriptor=lambda handle: transferred.append(handle) or 77)
    assert descriptor == 77
    assert transferred == [3]
    assert api.closed == [2, 1]


@pytest.mark.parametrize('invalid_index', [1, 2, 3])
def test_windows_capture_rejects_reparse_at_every_component(tmp_path, invalid_index):
    """Junction в родителе и symlink конечного файла одинаково запрещены."""
    api = HandleApi(invalid_index=invalid_index)
    with pytest.raises(DocumentError, match='reparse|Reparse'):
        secure_open(tmp_path, 'nested/file.md', api=api)
    assert set(api.closed) == set(range(1, len(api.opened) + 1))


def test_windows_capture_checks_path_boundaries_and_closes_handles(tmp_path):
    """Строковый общий префикс C:\\project-sibling не считается дочерним путём."""
    api = HandleApi(escaped_index=3)
    with pytest.raises(DocumentError, match='вне корня'):
        secure_open(tmp_path, 'nested/file.md', api=api)
    assert api.closed == [3, 2, 1]


@pytest.mark.parametrize('filename', ['../x', r'C:\x', r'C:x', r'\x', 'file.md:stream'])
def test_windows_capture_rejects_non_relative_and_ads_paths(tmp_path, filename):
    """Опасный путь отвергается до любого обращения к Win32."""
    api = HandleApi()
    with pytest.raises(DocumentError):
        secure_open(tmp_path, filename, api=api)
    assert not api.opened


def test_windows_capture_failed_descriptor_transfer_closes_file(tmp_path):
    """Ошибка CRT transfer не теряет исходный handle."""
    api = HandleApi()
    def fail(handle):
        raise OSError('fixture CRT error')
    with pytest.raises(DocumentError):
        secure_open(tmp_path, 'nested/file.md', api=api, to_descriptor=fail)
    assert api.closed == [3, 2, 1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows handles require Windows CI')
def test_real_windows_capture_without_python_path_assumptions(tmp_path):
    """На Windows копирует документ через Win32 и оставляет оригинал без изменений."""
    root = tmp_path / 'source'
    (root / 'nested').mkdir(parents=True)
    source = root / 'nested' / 'file.md'
    source.write_text('# Windows capture', encoding='utf-8')
    destination = tmp_path / 'captured.md'
    result = capture(root, 'nested/file.md', destination)
    assert destination.read_bytes() == source.read_bytes()
    assert result['size'] == source.stat().st_size
    destination.chmod(0o666)


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows sharing flags require Windows CI')
def test_real_windows_capture_handle_blocks_writes_and_rename(tmp_path):
    """Конечный handle запрещает запись/замену до завершения чтения и закрытия CRT fd."""
    source = tmp_path / 'file.md'
    source.write_text('immutable during capture')
    descriptor = secure_open(tmp_path, 'file.md')
    try:
        with pytest.raises(OSError):
            source.open('wb')
        with pytest.raises(OSError):
            source.rename(tmp_path / 'renamed.md')
        assert os.read(descriptor, 100) == b'immutable during capture'
    finally:
        os.close(descriptor)
    source.write_text('write permitted after capture')


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows reparse points require Windows CI')
def test_real_windows_capture_rejects_symlink(tmp_path):
    """Даже ссылка внутри проекта запрещена; Windows CI без symlink privilege явно skip."""
    source = tmp_path / 'file.md'
    source.write_text('source')
    try:
        (tmp_path / 'link.md').symlink_to(source)
    except OSError:
        pytest.skip('Windows symlink creation privilege unavailable')
    with pytest.raises(DocumentError):
        secure_open(tmp_path, 'link.md')
