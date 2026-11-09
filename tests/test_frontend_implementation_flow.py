"""Отдельная геометрия Flow реализаций проверяется в обычном наборе frontend-тестов."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_implementation_flow():
    """Проверяет control-flow, геометрию и переключение renderer с сохранением раскрытий."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_implementation_flow_layout.mjs"))],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Implementation Flow" in result.stdout
