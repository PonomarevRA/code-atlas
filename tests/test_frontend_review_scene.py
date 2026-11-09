"""Scene assembly regression verifies real modules without browser dependencies."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_review_scene_contracts():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_frontend_review_scene.mjs"))],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Frontend review scene contracts PASS" in result.stdout
