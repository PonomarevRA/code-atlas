"""Exercise optional mods through actual Flow and topology card renderers."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_mod_runtime_real_renderers():
    """Themes and trusted actions preserve raw IDs; failed hooks and safe mode remain recoverable."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend validation')
    result = subprocess.run([node, str(Path(__file__).with_name('test_mod_runtime.mjs'))], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'raw identity PASS' in result.stdout


def test_html_captured_facts_navigation_and_untrusted_text():
    """HTML-текст не исполняется, а переход сохраняет точный raw-ID блока."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend validation')
    result = subprocess.run([node, str(Path(__file__).with_name('test_html_facts.mjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'exact navigation' in result.stdout
