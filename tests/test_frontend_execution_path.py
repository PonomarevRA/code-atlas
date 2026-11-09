"""Статическое дерево возможных вызовов и безопасное отображение событий исходника."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_execution_path():
    """Проверяет цикл/повтор, ленивые события, реальные ID исходника и асинхронные гонки."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_execution_path_view.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Execution tree cycles' in result.stdout
