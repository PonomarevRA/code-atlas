"""Browser history protocol is checked locally without a browser or external dependencies."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_navigation_history():
    """Check URL/state validation, Back/Forward camera and expansion restoration, and async race guards."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Frontend navigation needs Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_navigation_history.mjs"))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "restore races PASS" in result.stdout
