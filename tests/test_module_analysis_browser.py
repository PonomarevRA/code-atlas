"""Opt-in source browser qualification without taking over the user's browser."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_agent_mcp import atlas_server


@pytest.mark.skipif(
    not os.environ.get("ATLAS_PLAYWRIGHT_MODULE") or not os.environ.get("ATLAS_TEST_CHROMIUM"),
    reason="Isolated browser runtime must be supplied explicitly",
)
def test_actual_module_analysis_page(atlas_server):
    """Use the real fixture core and clean browser, recording configured artifacts."""
    _, origin = atlas_server
    node = os.environ.get("ATLAS_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    environment = {**os.environ, "ATLAS_BROWSER_ORIGIN": origin}
    result = subprocess.run(
        [node, str(Path(__file__).with_name("browser_module_analysis.mjs"))],
        env=environment, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"status":"PASS"' in result.stdout
