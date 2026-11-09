"""Чистая проекция пути и совместимость GraphView без конструктора."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_path_scene():
    """Проверяет ветви/доказательства/лимиты, callbacks и сброс состояния нового снимка."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_path_scene.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Pure path scene and prototype-safe facade PASS" in result.stdout


def test_frontend_entry_flow_path():
    """Проверяет стабильные карточки входного пути и геометрию настоящих плавных связей."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_entry_flow_path.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Stable entry Flow path, curves, branches, evidence, camera and snapshots PASS" in result.stdout


def test_frontend_path_reading_filters():
    """Горизонтальное чтение сохраняет вложенные вызовы и камеру при независимом скрытии управления."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_path_reading_filters.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "independent control filters and stable geometry PASS" in result.stdout


def test_frontend_control_tree():
    """Вложенные ветви и пустые arms сохраняют дерево, вызовы и геометрию обоих провайдеров."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_path_control_tree.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Control trees, distinct alternatives, empty arms and stable provider geometry PASS" in result.stdout


def test_frontend_compact_path_transition():
    """Скрытые тела не раздвигают дерево; анимация сохраняет экранную позицию раскрываемого метода."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_path_transition.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Compact visible paths, anchored expansion, animations and reduced motion PASS" in result.stdout


def test_frontend_implementation_comparison():
    """Кандидаты одного интерфейса стоят в колонке; деревья деталей не пересекаются."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_implementation_comparison.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Vertical implementation comparison, nested bodies, filters and evidence PASS" in result.stdout


def test_frontend_local_expansion_comments():
    """Раскрытие сохраняет локальную ветвь; исходные комментарии объясняют реализации в карточке."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_path_local_expansion.mjs"))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Local neighbourhood expansion, vertical alternatives and source comment cards PASS" in result.stdout


def test_frontend_method_sequence():
    """One scene supplies occurrence numbers, frame returns and lexical jumps for both providers."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend verification')
    result = subprocess.run([node, str(Path(__file__).with_name('test_method_sequence.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'provider parity and real GraphView PASS' in result.stdout


def test_frontend_method_sequence_routing():
    """Loop/return corridors and wrapped rows avoid cards and preserve translated inline geometry."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend verification')
    result = subprocess.run([node, str(Path(__file__).with_name('test_method_sequence_routing.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'nested loops, returns, data, translated inline scenes and bounds PASS' in result.stdout


def test_frontend_sequence_readability():
    """Source wrapping, ownership and honest limits preserve caller steps and raw facts."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend verification')
    result = subprocess.run([node, str(Path(__file__).with_name('test_sequence_readability.mjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'visible frame ownership, limits, reuse and exact data relations PASS' in result.stdout


@pytest.mark.parametrize('filename', ['test_compact_sequence_cards.mjs', 'test_implementation_list.mjs', 'test_complexity_badge.mjs'])
def test_frontend_compact_symbols(filename):
    """Real SVG/HTML paths preserve ports, source actions and bounded implementation names."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for frontend verification')
    result = subprocess.run([node, str(Path(__file__).with_name(filename))], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS' in result.stdout
