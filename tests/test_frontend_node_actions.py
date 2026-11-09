"""Проверки раздельных действий блока и renderer иерархии на реальном GraphView."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("filename", ["test_node_actions.mjs", "test_inline_hierarchy_renderer.mjs"])
def test_frontend_node_actions(filename):
    """Проверяет bubbling/keyboard, исходные IDs, bounds, выделение и камеру."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name(filename))],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
