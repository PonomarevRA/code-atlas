"""Контракты пространственного индекса и ограниченного кэша маршрутов."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_map_performance():
    """Сверяет индекс с полным обходом и проверяет повтор/инвалидацию маршрутов."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_map_performance.mjs"))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

