"""Изолированные проверки добавочного слоя точек входа."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_ingress():
    """Проверяет сопоставленные цели, область, фильтр и страницы без изменения raw ID."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_ingress.mjs"))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Ingress overlay filtering and identity PASS" in result.stdout
