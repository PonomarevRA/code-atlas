"""UI настройки агента без изменения файлов редактора."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_frontend_agent_setup():
    """Проверяет безопасные загрузки, ошибки обновления и отмену устаревших результатов."""
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    result=subprocess.run([node,str(Path(__file__).with_name('test_agent_setup_view.mjs'))],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert 'stale selection PASS' in result.stdout
