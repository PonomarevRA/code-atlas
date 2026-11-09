"""Проверяет маршруты Flow через настоящий SVG renderer."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_implementation_routing():
    """Стрелки и подписи обходят карточки; ссылки сохраняют raw ID и масштаб."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_implementation_edge_routing.mjs"))],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "routing" in result.stdout
