"""Документационный режим объясняет реальные ветви и сохраняет контекст метода."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_method_documentation():
    """Проверяет сравнения, null, циклы, вложенные switch, экранирование и историю режима."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для frontend проверки нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_method_documentation.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'history PASS' in result.stdout
