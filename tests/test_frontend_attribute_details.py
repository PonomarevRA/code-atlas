"""Проверки frontend модуля атрибутов на Node.js без npm-зависимостей."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_attribute_details():
    """Проверяет реальные viewer и renderer, владельцев атрибутов и защиту от старых ответов."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для frontend проверки нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_attribute_details.mjs"))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "stale snapshot PASS" in result.stdout
