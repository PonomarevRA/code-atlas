"""Проверяет полную область вызовов и ограничения отображения без браузера."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_call_isolation():
    """Достижимость направлена; циклы конечны, кандидаты и полные счётчики сохраняются."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Frontend isolation needs Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_call_isolation.mjs"))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "bounded display PASS" in result.stdout
