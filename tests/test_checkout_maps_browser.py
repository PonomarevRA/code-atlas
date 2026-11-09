"""Real private checkout/settings composition in an isolated source browser."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_agent_mcp import atlas_server


@pytest.mark.skipif(
    not os.environ.get('ATLAS_PLAYWRIGHT_MODULE') or not os.environ.get('ATLAS_TEST_CHROMIUM'),
    reason='Isolated browser runtime must be supplied explicitly',
)
def test_temporary_checkout_settings(atlas_server, tmp_path):
    """Delete only the selected private folder while retaining source and canonical bytes."""
    _, origin = atlas_server
    node = os.environ.get('ATLAS_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node.js is unavailable')
    source = tmp_path / 'private-checkout-input'
    source.mkdir()
    program = source / 'amount.py'
    payload = b'def label(value):\n    """Describe the supplied value."""\n    return "positive" if value > 0 else "other"\n'
    program.write_bytes(payload)
    state = tmp_path / 'state'
    protected = [state / 'projects.json', *sorted((state / 'legacy-fixture' / 'first').glob('*.json'))]
    original = {path: path.read_bytes() for path in protected if path.is_file()}
    environment = {**os.environ, 'ATLAS_BROWSER_ORIGIN': origin, 'ATLAS_BROWSER_CHECKOUT': str(source)}
    result = subprocess.run([node, str(Path(__file__).with_name('browser_checkout_maps.mjs'))],
                            env=environment, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"status":"PASS"' in result.stdout
    assert program.read_bytes() == payload
    assert all(path.read_bytes() == before for path, before in original.items())

