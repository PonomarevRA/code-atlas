"""Exercise wiki task grouping against the actual shipped chapter bundle."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_task_grouped_wiki_navigation():
    """
    Run the pure JavaScript contract for all bundled and future guide chapters.

    Missing Node skips this frontend check. A failing contract includes the
    process output so source/bundle identity problems remain diagnosable.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for wiki navigation checks')
    result = subprocess.run([node, str(Path(__file__).with_name('test_project_wiki_navigation.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
