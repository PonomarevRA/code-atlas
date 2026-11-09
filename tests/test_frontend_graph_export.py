"""Ограничения PNG-экспорта и восстановление кнопки после ошибки."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_graph_export():
    """Проверяет большие размеры, отрицательные координаты и пустую карту."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_graph_export.mjs"))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
