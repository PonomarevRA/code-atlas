"""Крупный отчёт не снимает ограничение существующего просмотра исходников."""

from pathlib import Path

import pytest

from atlas.source_viewer import MAX_SOURCE_BYTES, SourceError, _read_regular_file, read_node_source


def test_report_budget_is_explicit_and_source_limit_is_preserved(tmp_path):
    """Отдельный report budget позволяет 2MiB, а прежний source API по-прежнему отказывает."""
    report = tmp_path / 'large.xml'
    content = b'x' * (MAX_SOURCE_BYTES + 1)
    report.write_bytes(content)
    with pytest.raises(SourceError) as error:
        read_node_source(tmp_path, {'file': 'large.xml'}, 's')
    assert error.value.status == 413
    assert _read_regular_file(tmp_path, Path('large.xml'), 8 * 1024 * 1024) == content
    with pytest.raises(SourceError) as error:
        _read_regular_file(tmp_path, Path('large.xml'), 8)
    assert error.value.status == 413


def test_report_budget_does_not_allow_symlink_escape(tmp_path):
    """Более высокий лимит сохраняет запрет открытия ссылки даже на существующий внешний файл."""
    root = tmp_path / 'repo'
    root.mkdir()
    outside = tmp_path / 'secret.xml'
    outside.write_text('EXTERNAL_SENTINEL')
    (root / 'report.xml').symlink_to(outside)
    with pytest.raises(SourceError) as error:
        _read_regular_file(root, Path('report.xml'), 8 * 1024 * 1024)
    assert error.value.status == 403
