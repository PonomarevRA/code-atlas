"""Изолированная проверка управления доступными боковыми панелями."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('filename', ['test_workspace_panels.mjs', 'test_map_chrome.mjs'])
def test_workspace_panels(filename):
    """Проверяет Escape, focus, aria/inert, responsive preferences и отсутствие двойного toggle."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name(filename))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout
