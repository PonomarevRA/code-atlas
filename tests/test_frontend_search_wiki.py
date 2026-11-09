"""Поиск → captured wiki → код: отмена, безопасный текст и bounded browser state."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_search_wiki_journeys():
    """Проверяет переход из систем, late fetch, URL wiki и отсутствие HTML исполнения."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for frontend modules")
    result = subprocess.run([node, str(Path(__file__).with_name("test_search_wiki_journeys.mjs"))], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Search wiki journeys PASS" in result.stdout
