"""Блоки методов пути: if и вызовы остаются внутри метода."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_path_rows():
    """Проверяет, что if и вызовы лежат в блоке метода, а повторный вызов остаётся ссылкой."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_path_row_layout.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Path row layout PASS' in result.stdout
