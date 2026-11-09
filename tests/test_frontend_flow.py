"""Проверки направленной карточной раскладки на циклах и больших страницах."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_frontend_flow_layout():
    """Проверяет SCC, порты, полные границы кривых и устойчивость при перестановке входа."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_flow_layout.mjs'))], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Flow layout SCC, direction, bounds and deterministic 320 cards PASS' in result.stdout


def test_frontend_flow_renderer():
    """Проверяет реальные SVG-карточки, каждую стрелку, overlays и переключение обратно."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_flow_renderer.mjs'))], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Flow renderer ports, source colors, identity and provider switch PASS' in result.stdout


def test_frontend_flow_routes():
    """Проверяет тесные циклы, отсутствие самопересечений и обход карточек настоящим renderer."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_flow_routes.mjs'))], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Flow routes narrow cycles, self-intersections, obstacles and actual renderer PASS' in result.stdout


def test_frontend_shared_curve_rule():
    """Проверяет общий расчёт двух опор и отсутствие волны в свободном пролёте."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_edge_curve_rule.mjs'))], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Shared thirds rule, no central wave, renderer parity and ingress ports PASS' in result.stdout
