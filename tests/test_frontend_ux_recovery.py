"""Focused UX regressions for wiki modal isolation and catalogue controls."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_wiki_modal_and_catalogue_names():
    """Tab/Escape, inert restoration and accessible paging names retain their contracts."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for frontend modules")
    result = subprocess.run([node, str(Path(__file__).with_name("test_wiki_dialog_focus.mjs"))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Wiki modal integration and catalogue action names PASS" in result.stdout
