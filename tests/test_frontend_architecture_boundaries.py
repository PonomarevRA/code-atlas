"""Регрессии контракта выделенных модулей workspace и HTTP ожидания."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_architecture_boundaries():
    """Свидетельства и повторы остаются фактами; pending и отмена не оставляют вечный loading."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_architecture_boundaries.mjs'))], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Architecture boundaries PASS' in result.stdout
