"""Run agentless engine settings controls through the actual page/controller JavaScript."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_semantic_engine_settings_page():
    """Save/reset, snapshot checks, bounded polling and detached-panel guards remain operational."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend validation')
    result = subprocess.run([node, str(Path(__file__).with_name('test_semantic_engine_settings.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Management-page composition' in result.stdout
