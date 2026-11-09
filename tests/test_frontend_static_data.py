"""Статические таблицы используют тот же renderer и геометрию, что дерево методов."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_static_data():
    """Значения переживают фильтры условий; обе раскладки сохраняют непересекающиеся карточки."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Frontend checks need Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_path_static_data.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'both layouts PASS' in result.stdout
