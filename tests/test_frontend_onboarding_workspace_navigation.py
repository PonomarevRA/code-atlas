"""Contract checks for the canvas viewport and reader-controller integration."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_onboarding_workspace_navigation():
    """Check actual overlay selectors, focus panning and exact controller return addresses."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend navigation checks')
    result = subprocess.run([node, str(Path(__file__).with_name('test_onboarding_workspace_navigation.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS' in result.stdout
