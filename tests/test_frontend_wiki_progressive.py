"""Проверяет независимую загрузку captured wiki и смысловых рекомендаций."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_progressive_wiki():
    """Ошибка/late ответ semantic поиска сохраняют документы и текущий переход."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for frontend modules")
    result = subprocess.run([node, str(Path(__file__).with_name("test_wiki_progressive.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Progressive wiki PASS" in result.stdout
