"""Семантическая карта и hover-popup проверяются вместе с остальным frontend."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("script", ["test_code_semantics.mjs", "test_map_tooltip.mjs"])
def test_frontend_map_understanding(script):
    """Запускает реальные JS-модули для ролей, геометрии и безопасных действий popup."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name(script))],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout
