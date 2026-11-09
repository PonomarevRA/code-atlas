"""Discover the mod wiki's pure renderer/link contract in the ordinary pytest suite."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_mod_wiki_links_and_text_rendering():
    """Reject unsafe destinations while preserving exact mod paths, anchors and plain text."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for frontend modules")
    script = Path(__file__).with_name("test_project_wiki_mods.mjs")
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "text-only Markdown PASS" in result.stdout
