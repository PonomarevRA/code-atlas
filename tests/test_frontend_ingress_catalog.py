"""Каталог HTTP/Kafka/Rabbit не зависит от текущей области или режима карты."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_ingress_catalog():
    """Проверяет полные счётчики, поиск, страницы и фильтрованные реальные обработчики."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_ingress_catalog.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Repository ingress catalog completeness' in result.stdout
