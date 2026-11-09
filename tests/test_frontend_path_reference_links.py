"""Связь локальных reference-карточек с реальными методами в обоих режимах."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_path_reference_links():
    """Проверяет непрерывность, candidate, повторы, циклы и неизменность камеры."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_path_reference_links.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'Path reference links' in result.stdout
