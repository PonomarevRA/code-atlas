"""Grouped topology checks use the real GraphView renderer, without browser dependencies."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_topology_groups():
    """Verify grouping, card bounds, all raw edges and stable renderer interactions."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Frontend checks require Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_topology_groups.mjs"))],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Topology groups" in result.stdout
