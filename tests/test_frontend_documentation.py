"""Изолированные проверки панели документации MCP."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_frontend_documentation():
    """Проверяет явный выбор инструмента, keywords-only и асинхронные границы контекста."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для frontend проверки нужен Node.js')
    result = subprocess.run([node,str(Path(__file__).with_name('test_documentation_view.mjs'))],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert 'stale context PASS' in result.stdout
