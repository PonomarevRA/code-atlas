"""Проверяет проектные блоки и точность агрегированных фрагментов через Node.js."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_project_blocks():
    """Проверяет владение, границы, межпроектные связи и точность Git-счётчиков."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_project_blocks.mjs"))], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Project blocks PASS" in result.stdout
