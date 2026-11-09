"""Check typed node actions through the real GraphView and compact navigation contract."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_map_blocks():
    """Separate selection/navigation and verify real containment, full scope and stale history."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Frontend integration needs Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_map_blocks.mjs"))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "bounded history PASS" in result.stdout
