"""Browser handoff preserves an independently initialized map without sharing a browser history object."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_browser_map_link():
    """Exercise compact handoff, exact disclosure state and rejection of malformed fragments."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend verification")
    result = subprocess.run([node, str(Path(__file__).with_name("test_browser_map_link.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "safe fragments PASS" in result.stdout
