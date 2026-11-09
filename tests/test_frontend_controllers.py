"""Контроллеры UI и отклонение ответов после смены пользовательского контекста."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_controllers():
    """Проверяет два конкурентных ingress-запроса, handler переход и очистку Git."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_controllers.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Controllers stale response and real handler navigation PASS' in result.stdout
