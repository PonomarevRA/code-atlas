"""Эквивалентность индексированной модели исходной семантике графа."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_graph_model():
    """Проверяет комбинации фильтров, направления, self-loop, дубли и смену снимка."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_graph_model.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Graph model indexed semantic parity PASS' in result.stdout
