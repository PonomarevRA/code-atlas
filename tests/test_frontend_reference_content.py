"""Проверка безопасного представления DocFX/compiled XML в обеих страницах."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_reference_content():
    """Node DOM проверяет структуру и literal data; настоящий браузер проверяется отдельно."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js unavailable')
    result = subprocess.run([node, str(Path(__file__).with_name('test_reference_content.mjs'))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'PASS' in result.stdout
