"""Проверяет изоляцию regex и семантику поиска по именам в настоящем JS Worker."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_regex_search_worker():
    """Ошибки, старые ответы и backtracking не меняют данные и не блокируют основной поток."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки regex Worker нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_regex_search.mjs"))],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Regex worker isolation PASS" in result.stdout


def test_regex_overlay_preserves_camera_nodes_and_edges():
    """Методы GraphView изменяют только кольца и счётчики, не структуру карты."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_regex_view.mjs"))],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Regex overlays preserve geometry PASS" in result.stdout
