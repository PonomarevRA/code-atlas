"""Frontend описание узла и аннотации ветвей без дополнительных npm-зависимостей."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_frontend_node_description():
    """Проверяет реальные callbacks, самостоятельный endpoint и асинхронную изоляцию."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для frontend проверки нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_node_description_view.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'stale requests PASS' in result.stdout
