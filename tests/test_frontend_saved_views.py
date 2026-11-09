"""Проверка immutable SVG и UI сохранения в изолированном DOM."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_frontend_saved_views():
    """Геометрия и текст сохраняются; поздний POST не меняет новый выбор."""
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    result=subprocess.run([node,str(Path(__file__).with_name('test_saved_views.mjs'))],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert 'stale create PASS' in result.stdout
