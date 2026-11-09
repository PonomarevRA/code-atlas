"""
Discover the pure architecture-cluster geometry contracts through the regular Python test suite.
"""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_architecture_cluster_geometry_contracts():
    """
    Verify both renderer footprints, stable anchors, bounded occupancy and semantic cluster labels.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for architecture geometry modules')
    script = Path(__file__).with_suffix('.mjs')
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'Architecture semantic cluster geometry PASS' in result.stdout
    assert 'packages/unknown nodes use neutral other grouping PASS' in result.stdout
    assert 'Hidden overflow pin restoration and bounded24 registry pin metadata PASS' in result.stdout
    assert 'Filtered short owner retains full240px heading region and group start spacing PASS' in result.stdout
