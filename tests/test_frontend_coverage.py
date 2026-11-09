"""Standalone coverage UI exercises actual JavaScript view/controller contracts."""
from pathlib import Path
import subprocess


def test_coverage_frontend_behavior():
    """Run DOM/action regressions rather than checking implementation-shaped strings only."""
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", str(root / "tests/test_coverage_view.mjs")], cwd=root, check=True)
