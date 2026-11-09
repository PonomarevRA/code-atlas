"""Проверка контракта нового обзора групп без установки браузерных пакетов."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_group_overview():
    """Проверяет отсутствие скрытого усечения, фильтры, SVG и адресуемые страницы."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Frontend checks require Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_group_overview.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
