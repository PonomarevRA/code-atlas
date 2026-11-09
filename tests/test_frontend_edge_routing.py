"""Проверки стабильных кривых без изменений раскладки."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_edge_routing():
    """Проверяет концы, коридоры, направления, повторяемость и конечность кривых."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_edge_routing.mjs"))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Edge routing PASS" in result.stdout
