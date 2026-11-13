"""Qualify short bilingual release notes consumed by native update discovery."""
from pathlib import Path

import pytest

from tools.release_changelog import release_changelog


def test_current_release_has_bilingual_user_notes():
    """Require the actual checkout to ship notes for the packaged version."""
    import json
    root = Path(__file__).resolve().parents[1]
    version = json.loads((root / 'packaging/desktop-product.json').read_bytes())['version']
    notes = release_changelog(root / 'CHANGELOG.md', version)
    assert notes.startswith(f'## [{version}]\n') and '### Русский' in notes and '### English' in notes


@pytest.mark.parametrize('bad_notes', [
    '### Русский\n- Изменение\n',
    '### Русский\n- Изменение\n### English\n- <a>link</a>\n',
    '### Русский\n- Изменение\n### English\n- ' + 'x' * 241,
    '### Русский\n- Изменение\n### English\n' + '- Change\n' * 6,
])
def test_reject_incomplete_or_unbounded_notes(tmp_path, bad_notes):
    """Release metadata cannot carry oversized or markup-heavy notification text."""
    path = tmp_path / 'CHANGELOG.md'
    path.write_text('## [0.1.7]\n\n' + bad_notes, encoding='utf-8')
    with pytest.raises(ValueError):
        release_changelog(path, '0.1.7')
