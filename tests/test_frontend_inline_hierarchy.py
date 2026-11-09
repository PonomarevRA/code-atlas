"""Модель и геометрия inline-вложенности входят в frontend CI без браузерных зависимостей."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize('filename', ['test_inline_hierarchy.mjs', 'test_inline_hierarchy_layout.mjs', 'test_inline_hierarchy_renderer.mjs', 'test_inline_flow_layout.mjs', 'test_flow_card.mjs', 'test_field_table.mjs', 'test_node_card_layout.mjs'])
def test_frontend_inline_hierarchy(filename):
    """Проверяет настоящие чистые JS-модули, включая containment, cycles и bounded geometry."""
    node=shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result=subprocess.run([node,str(Path(__file__).with_name(filename))],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS' in result.stdout
