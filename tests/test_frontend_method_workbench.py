"""Contract tests for bounded reading independently of the global map viewport."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('filename', ['test_method_reader_source.mjs', 'test_method_workbench.mjs'])
def test_frontend_method_workbench(filename):
    """Check reader source isolation and local comparison/navigation acceptance."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend contract verification')
    result = subprocess.run([node, str(Path(__file__).with_name(filename))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS' in result.stdout
