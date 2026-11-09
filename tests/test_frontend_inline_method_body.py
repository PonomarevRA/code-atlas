"""Общий renderer рисует тело внутри карты, сохраняя raw источник и snapshot."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_inline_method_body():
    """Конструктор, ветви, вложенный метод и ключи словаря проходят настоящий GraphView."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Frontend checks need Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_inline_method_body.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'stale requests PASS' in result.stdout
