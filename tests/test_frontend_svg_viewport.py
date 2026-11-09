"""Проверяет оконный SVG без изменения полной геометрии и экспорта."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_svg_viewport():
    """Проверяет возврат тех же карточек, экранное окно и восстановление после ошибки экспорта."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_svg_viewport.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
