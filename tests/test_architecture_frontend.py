"""Выполняет браузерный state-contract через принятые Node fixtures проекта."""
import subprocess
from pathlib import Path


def test_architecture_page_states():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['node', 'tests/test_architecture_page.mjs'], cwd=root, check=True, timeout=30)
