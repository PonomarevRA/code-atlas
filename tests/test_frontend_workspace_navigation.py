"""Прямой контракт восстановления workspace поверх реального GraphView."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_workspace_navigation():
    """Проверяет раскрытия, камеру, исходные связи и отмену устаревшего перехода."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_workspace_navigation.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Workspace navigation capture" in result.stdout
