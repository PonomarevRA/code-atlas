"""
Run the pure architecture export contract in the normal Python suite.
"""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_pure_architecture_export():
    """
    Check deterministic, bounded and safe text serialization with the local Node runtime.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for architecture export contracts')
    result = subprocess.run([node, str(Path(__file__).with_suffix('.mjs'))], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'Architecture export PASS' in result.stdout
