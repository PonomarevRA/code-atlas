"""Интеграция реального SVG выбора/раскрытия с browser history и перемещением камеры."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_navigation_integration():
    """Проверяет Enter/Space, pan без push и Back/Forward камеры/раскрытий через настоящие JS-модули."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_navigation_integration.mjs"))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "Back/Forward camera, raw selection and expanded path PASS" in result.stdout
