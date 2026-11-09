"""Проверка уровня решения и сохранности исходной архитектуры."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_solution_architecture():
    """Репозитории сворачиваются без внутренних связей, с весами, участниками и поиском."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    result = subprocess.run([node, str(Path(__file__).with_name('test_solution_architecture.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
