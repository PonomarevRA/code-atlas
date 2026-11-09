"""Навигация по карте стрелками входит в обычную frontend-проверку."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_keyboard_pan():
    """Проверяет настоящий keydown listener, ограничения фокуса и сохранение состояния карты."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_keyboard_pan.mjs"))],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Keyboard map pan" in result.stdout
