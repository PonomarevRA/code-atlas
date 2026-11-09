"""Запускает чистые JS-проверки геометрии через установленный Node.js."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_layout_geometry():
    """Проверяет сохранность данных, детерминизм, цвета и разведение групп."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend-геометрии нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_layout.mjs"))],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "Layout geometry PASS" in result.stdout
