"""Exercise user-facing HTTP recovery with the production JavaScript request helper."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_http_errors_preserve_status_without_json():
    """HTML/null failure bodies cannot hide status or leak parser details into recovery UI."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for request helper checks')
    result = subprocess.run([node, str(Path(__file__).with_suffix('.mjs'))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Bounded request recovery PASS' in result.stdout


def test_empty_map_states_have_correct_recovery():
    """Loading, failure and an actually empty saved graph retain separate user actions."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for map state checks')
    result = subprocess.run([node, str(Path(__file__).with_name('test_map_empty_state.mjs'))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Map empty-state recovery PASS' in result.stdout


def test_navigation_menu_stays_inside_viewport():
    """Both horizontal edges and a short window retain bounded menu geometry."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for menu placement checks')
    result = subprocess.run([node, str(Path(__file__).with_name('test_menu_placement.mjs'))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Menu viewport placement PASS' in result.stdout
