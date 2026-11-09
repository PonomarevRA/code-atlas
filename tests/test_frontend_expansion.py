"""Контекстное раскрытие не меняет факты графа и точно восстанавливает обзор."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize('script,marker', [('test_expansion.mjs', 'Expansion raw facts'), ('test_expansion_view.mjs', 'Expansion SVG context')])
def test_frontend_expansion(script, marker):
    """Проверяет чистую проекцию и настоящие SVG-рендереры обоих провайдеров."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name(script))], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert marker in result.stdout
