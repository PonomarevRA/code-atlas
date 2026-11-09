"""Проверки отображения исходников в изолированном JS DOM."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_source_view():
    """Проверяет строки, безопасный текст, смену узлов/снимка и закрытие панели."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_source_view.mjs"))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Source viewer safety and stale responses PASS" in result.stdout
