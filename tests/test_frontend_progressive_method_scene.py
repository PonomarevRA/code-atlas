"""Source-derived progressive expansion preserves an existing diagram's reading context."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_progressive_method_scene():
    """Check scene contracts, both renderers, placement history and pinned async facts."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend contract verification')
    result = subprocess.run([node, str(Path(__file__).with_name('test_progressive_method_scene.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Progressive method scene' in result.stdout and 'PASS' in result.stdout
