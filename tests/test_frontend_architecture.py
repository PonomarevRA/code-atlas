"""Структурный обзор и пагинация файлов/исходных символов."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_architecture():
    """Проверяет уровни, пресеты, полноту страниц и реальные ID исходников."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_architecture.mjs"))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Architecture drilldown and complete pagination PASS" in result.stdout
