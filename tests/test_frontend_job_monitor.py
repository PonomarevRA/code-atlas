"""Повторное наблюдение за worker после кратковременного отказа сети."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_job_monitor():
    """Проверяет сохранение задачи, отмену наблюдения и ограниченную задержку повторов."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_job_monitor.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Job monitor transient retry and bounded backoff PASS' in result.stdout
