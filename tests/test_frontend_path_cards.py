"""Независимая проверка SVG-отрисовки раскрываемого пути."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_path_cards():
    """Проверяет камеру, клавиатуру, циклы, кандидатов и непересекающиеся карточки."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_path_cards.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Path cards inline expansion' in result.stdout
