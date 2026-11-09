"""Проверка цветов по именам и неизменности геометрии."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_colors():
    """Проверяет Unicode, стабильность, семейства проектов и контраст текста."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_colors.mjs"))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Name colors PASS" in result.stdout
