"""Запускает независимую от браузера проверку асинхронной панели поиска."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_semantic_search_panel():
    """Проверяет отмену старых ответов и безопасный показ источников."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required")
    result = subprocess.run([node, str(Path(__file__).with_name("test_semantic_search.mjs"))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "stale context PASS" in result.stdout
