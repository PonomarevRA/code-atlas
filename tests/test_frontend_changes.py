"""Чистые JS-проверки подсветки Git и пересечения с фильтрами."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_git_highlights_and_filters():
    """Отметки и счётчики учитывают только видимые узлы, исходный граф неизменен."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_changes.mjs"))],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "Git highlights PASS" in result.stdout
