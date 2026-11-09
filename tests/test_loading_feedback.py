"""Run meaningful JavaScript loading lifecycle contracts in the normal pytest suite."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('script,marker', [
    ('test_loading_state.mjs', 'Loading lifecycle PASS'),
    ('test_loading_status.mjs', 'Loading presentation PASS'),
    ('test_semantic_loading_status.mjs', 'Semantic loading status PASS'),
])
def test_loading_feedback_contract(script, marker):
    """
    Check request settlement, nonblocking presentation and explicit background observation.

    script is an authored sibling fixture; marker confirms it reached its final
    behavioral assertions. Missing Node is an explicit environment skip.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for loading feedback checks')
    result = subprocess.run([node, str(Path(__file__).with_name(script))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert marker in result.stdout
