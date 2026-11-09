"""Полное раскрытие цепочки и оформление возможных реализаций в обоих renderer."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_full_chain():
    """Проверяет сохранение камеры, выбор, циклы, ограничения и статическое происхождение."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_full_chain.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Full chain expansion and candidate provenance PASS' in result.stdout
