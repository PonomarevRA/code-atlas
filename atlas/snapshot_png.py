"""Локальный PNG-экспорт сохранённой геометрии без браузера и сетевых ресурсов."""

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

RENDER_VERSION = '4'


def render_png(view: dict, data_dir: Path) -> Path:
    """Рисует неизменяемый снимок через локальный Pillow runtime и атомарно кэширует PNG."""
    identifier = str(view.get('id', ''))
    if not re.fullmatch(r'[a-f0-9-]{32,36}', identifier):
        raise ValueError('Некорректный ID снимка')
    layout = view['layout']
    width, height = int(layout['width']), int(layout['height'])
    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 32_000_000:
        raise ValueError('Размер PNG превышает лимит')
    cache = Path(data_dir) / 'view-images'
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f'{identifier}-v{RENDER_VERSION}.png'
    if target.is_file():
        return target
    from .runtime_paths import resource_root, worker_executable, worker_creation_flags
    bundled_font = resource_root() / "packaging" / "assets" / "NotoSans-Regular.ttf"
    if getattr(sys, "frozen", False):
        # Перенесённый png-runtime.json может ссылаться на Python старой машины.
        # Standalone использует только собственный Pillow и лицензированный шрифт.
        python, font = worker_executable(), str(bundled_font)
    else:
        configuration = Path(data_dir) / 'png-runtime.json'
        settings = json.loads(configuration.read_text()) if configuration.is_file() else {}
        python = os.environ.get('ATLAS_PNG_PYTHON') or settings.get('python')
        if not python and importlib.util.find_spec('PIL'):
            python = sys.executable
        font = settings.get('font') or str(bundled_font if bundled_font.is_file() else Path('/System/Library/Fonts/Supplemental/Arial.ttf'))
    if not python or not Path(python).is_file():
        raise RuntimeError('Для PNG настройте локальный Python с Pillow через ATLAS_PNG_PYTHON или png-runtime.json.')
    if not Path(font).is_file():
        raise RuntimeError('Для PNG укажите локальный TTF-шрифт в png-runtime.json.')
    # Отдельный временный каталог исключает конфликт двух экспортов одного снимка.
    with tempfile.TemporaryDirectory(prefix='render-', dir=cache) as temporary:
        source = Path(temporary) / 'view.json'
        output = Path(temporary) / 'image.png'
        source.write_text(json.dumps(view, ensure_ascii=False), encoding='utf-8')
        try:
            result = subprocess.run([str(python), str(Path(__file__).with_name('snapshot_png_worker.py')),
                                     str(source), str(output), str(font)], capture_output=True, timeout=30,
                                    creationflags=worker_creation_flags())
        except subprocess.TimeoutExpired:
            raise RuntimeError('PNG-экспорт превысил 30 секунд.') from None
        if result.returncode or not output.is_file():
            raise RuntimeError('Локальный PNG-рендерер завершился с ошибкой. Проверьте Python, Pillow и шрифт.')
        os.replace(output, target)
    return target
