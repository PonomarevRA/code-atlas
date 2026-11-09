"""Изолированные frontend-проверки системного слоя без browser/npm-зависимостей."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_frontend_system_architecture():
    """Проверяет подсчёты, синтетические ID и привязку ответа к снимку."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для frontend проверки нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_system_architecture_view.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'stale requests PASS' in result.stdout
