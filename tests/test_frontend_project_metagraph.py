"""Проверки направленного метаграфа проектов через Node.js."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_project_metagraph():
    """Проверяет направление, полные веса и влияние связей на позиции контейнеров."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_project_metagraph.mjs"))], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Project metagraph PASS" in result.stdout
