"""Selection, source navigation and loop feedback remain separate UI actions."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_sequence_card_actions():
    """Verify real GraphView selection/keyboard, raw isolation roots and loop jump destinations."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend verification")
    result = subprocess.run([node, str(Path(__file__).with_name("test_sequence_card_actions.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "isolation PASS" in result.stdout
