"""Режимы карты и навигационные состояния интерфейса."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_frontend_view_modes():
    """Проверяет совместимость старого режима и сохранение скрытых фильтров."""
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    result=subprocess.run([node,str(Path(__file__).with_name('test_view_modes.mjs'))],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
