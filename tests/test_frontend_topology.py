"""Проверяет топологию, детерминизм и предельный размер локальной JS-раскладки."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_topology_layout_acceptance():
    """Плотные модули разделяются, компоненты упакованы, все узлы остаются на карте."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend-раскладки нужен Node.js")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_topology_layout.mjs"))],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "Topology layout PASS" in result.stdout
