"""Связи выбранного метода и направление к отдельным реализациям."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_path_relationships():
    """Проверяет raw caller/callee, shared/cycle references, раскрытие и навигацию."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_path_relationships.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Path relationships' in result.stdout
