"""PNG из сохранённой геометрии: реальный локальный renderer и повторяемый кэш."""
import os
import sys
import json
from pathlib import Path
import struct
import shutil
import subprocess
import pytest
from atlas.agent_views import build_view
from atlas.snapshot_png import render_png
from atlas.snapshot_png_worker import route
from agent_view_fixture import view_graph,view_request


def test_saved_png_signature_dimensions_and_cache(tmp_path,monkeypatch):
    """Экспорт использует реальные Pillow и шрифт; повторное чтение не запускает worker."""
    pytest.importorskip('PIL', reason='PNG rendering requires Pillow in this interpreter')
    runtime=Path(sys.executable)
    monkeypatch.setenv('ATLAS_PNG_PYTHON',str(runtime))
    view=build_view(tmp_path,view_graph(),view_request(annotations=[{'id':'n','title':'Пояснение','text':'Текст агента отдельно от фактов','nodeIds':['b']}]))
    output=render_png(view,tmp_path)
    content=output.read_bytes()
    assert content[:8]==b'\x89PNG\r\n\x1a\n'
    assert content[12:16]==b'IHDR'
    assert struct.unpack('>II',content[16:24])==(view['layout']['width'],view['layout']['height'])
    def forbidden(*args,**kwargs):
        """Готовый immutable PNG не должен вычисляться повторно."""
        raise AssertionError('cached export restarted renderer')
    monkeypatch.setattr('atlas.snapshot_png.subprocess.run',forbidden)
    assert render_png(view,tmp_path)==output
    assert output.read_bytes()==content
    assert not list((tmp_path/'view-images').glob('render-*'))


def test_png_rejects_traversal_and_oversize_before_worker(tmp_path):
    """Ошибочный ID или площадь не создают файл и не требуют зависимостей рендера."""
    for view in [{'id':'../escape','layout':{'width':1,'height':1}}, {'id':'a'*32,'layout':{'width':8192,'height':8192}}]:
        with pytest.raises(ValueError):
            render_png(view,tmp_path)
    assert not list(tmp_path.rglob('*.png'))


def test_saved_svg_and_png_curve_parity():
    """SVG и PNG совпадают во всех 33 точках прямого пролёта, возврата и нижнего обхода."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для сравнения с SVG нужен Node.js')
    source = {'x': 40, 'y': 60, 'width': 240, 'height': 80}
    targets = [
        {'x': 340, 'y': 210, 'width': 240, 'height': 80},
        {'x': 120, 'y': 340, 'width': 240, 'height': 80},
        {'x': 640, 'y': 60, 'width': 240, 'height': 80},
    ]
    script = """
import { savedEdgePath } from './web/saved-view-renderer.js';
import { readFileSync } from 'node:fs';
const input = JSON.parse(readFileSync(0, 'utf8'));
const values = input.targets.map(target => savedEdgePath(input.source, target).match(/-?\\d+(?:\\.\\d+)?/g).map(Number));
process.stdout.write(JSON.stringify(values));
"""
    result = subprocess.run([node, '--input-type=module', '-e', script],
                            input=json.dumps({'source': source, 'targets': targets}),
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    for target, controls in zip(targets, json.loads(result.stdout), strict=True):
        for step, point in enumerate(route(source, target)):
            t, u = step / 32, 1 - step / 32
            expected = [u**3 * controls[axis] + 3*u*u*t * controls[2+axis]
                        + 3*u*t*t * controls[4+axis] + t**3 * controls[6+axis] for axis in (0, 1)]
            assert point == pytest.approx(expected)
