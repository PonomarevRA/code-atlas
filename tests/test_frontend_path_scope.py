"""Проекция раскрытия методов без замены контекста схемы."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_path_scope():
    """Проверяет общие цели, циклы, варианты реализации и сохранение исходных фактов."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_path_scope.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Path scope inline expansion' in result.stdout
