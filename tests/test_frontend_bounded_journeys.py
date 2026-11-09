"""Pending HTTP и JSON завершают поиск входов, анализ пути и восстановление Browser Back."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_bounded_journeys():
    """Проверяет переход loading→error/retry, выход из restoring и запрет поздней публикации."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_bounded_journeys.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Bounded journeys PASS' in result.stdout
