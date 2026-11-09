"""Contract checks for reader return and explicit local camera navigation."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_onboarding_navigation():
    """Verify exact call occurrences and reversible focus through public actions."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend navigation checks')
    result = subprocess.run([node, str(Path(__file__).with_name('test_onboarding_navigation.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS' in result.stdout
