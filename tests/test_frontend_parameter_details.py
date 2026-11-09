"""Независимая проверка параметров/аргументов и немодального окна фактов."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_parameter_details():
    """Проверяет текст исходника, raw ID, отмену ответов и отсутствие прокрутки карты."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_parameter_details_view.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Parameter details expressions' in result.stdout
