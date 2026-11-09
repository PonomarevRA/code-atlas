"""Проверяет органическую раскладку, настоящий renderer и навигационный контракт."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_natural():
    """Карточки и связи сохраняются; фильтры стабильны, раскрытие уплотняет Natural-путь."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_natural_layout.mjs"))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "stable path and navigation PASS" in result.stdout
