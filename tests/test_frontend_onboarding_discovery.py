"""Verify source-backed discovery and cross-protocol hints without changing map filters."""
from pathlib import Path
import shutil
import subprocess
import pytest

def test_discovery_contract():
    """Run the independent semantic cases in Node when the runtime is available."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    subprocess.run([node, str(Path(__file__).with_suffix('.mjs').with_name('test_onboarding_discovery.mjs'))], check=True)
