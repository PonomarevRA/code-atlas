"""Check shared SVG badge semantics with the same minimal DOM as other render tests."""
import subprocess
from pathlib import Path


def test_health_badges_preserve_metric_identity():
    """Unknown health must not display the old red complexity score under an H selector."""
    result = subprocess.run(['node', str(Path(__file__).with_name('frontend_health_badges.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
