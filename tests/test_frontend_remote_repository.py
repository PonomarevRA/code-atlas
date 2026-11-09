"""Remote Git UI состояния без реальной сети."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_remote_repository_view():
    """Pending clone и reload с временной ошибкой сохраняют корректный busy/selection."""
    node=shutil.which('node')
    if not node:pytest.skip('Node.js unavailable')
    result=subprocess.run([node,str(Path(__file__).with_name('test_remote_repository_view.mjs'))],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
