"""Символьный поиск по репозиторию и текущему фильтру без изменения карты."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_navigation_search():
    """Проверяет default scope, сброс по snapshot и сохранение страницы при новых массивах."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_navigation_search.mjs"))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "stable pagination PASS" in result.stdout
