"""Запускает проверки фильтров без браузера, сети и npm-зависимостей."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_filter_semantics():
    """Сочетания проекта, ключевых слов, типа и связей сохраняют точные счётчики."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend-фильтров нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_filters.mjs"))],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "Filter semantics PASS" in result.stdout
