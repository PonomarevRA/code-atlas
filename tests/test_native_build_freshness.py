"""Ordinary release builds cannot reuse stale PYZ code when source mtimes are preserved."""
import importlib.util
import os
from pathlib import Path
import sys

import pytest
pytest.importorskip('PyInstaller')
from PyInstaller.archive.readers import ZlibArchiveReader

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('atlas_native_build_freshness', ROOT / 'packaging/build.py')
BUILD = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(ROOT / 'packaging'))
try:
    SPEC.loader.exec_module(BUILD)
finally:
    sys.path.remove(str(ROOT / 'packaging'))


def test_ordinary_release_build_refreshes_preserved_mtime_source(tmp_path, monkeypatch, capsys):
    """Exercise the real release command and PYZ writer twice, without a --clean CLI flag."""
    packaging = tmp_path / 'packaging'
    (packaging / 'assets').mkdir(parents=True)
    for filename in ('NotoSans-Regular.ttf', 'NotoSans-LICENSE.txt'):
        (packaging / 'assets' / filename).write_text('fixture')
    installer = {'darwin': 'install-macos.sh', 'win32': 'install-windows.ps1'}.get(sys.platform, 'install-linux.sh')
    (packaging / installer).write_text('fixture installer')
    source = tmp_path / 'fixture.py'
    source.write_text("VALUE = 'old snapshot'\n")
    original_stamp = source.stat().st_mtime_ns
    (packaging / 'code_atlas.spec').write_text(
        "from PyInstaller.building.api import PYZ\n"
        "from PyInstaller.config import CONF\n"
        "CONF['code_cache'] = {}\n"
        "PYZ([('fixture', str(__import__('pathlib').Path(SPECPATH).parent / 'fixture.py'), 'PYMODULE')])\n")
    monkeypatch.setattr(BUILD, 'ROOT', tmp_path)
    monkeypatch.setattr(BUILD, 'build_overlay', lambda *args, **kwargs: {})
    monkeypatch.setattr(BUILD, 'restore_native_resources', lambda *args, **kwargs: {})
    # This harness isolates PYZ freshness; exact privacy assets are qualified by their separate packaging tests.
    monkeypatch.setattr(BUILD, 'restore_privacy_pack', lambda *args, **kwargs: None)
    monkeypatch.setattr(BUILD, 'restore_human_auth_pack', lambda *args, **kwargs: None)
    # This harness qualifies PYZ freshness, not an installed C# SDK. Physical
    # subprocess resources remain real files consumed by the build manifest.
    monkeypatch.setattr(BUILD, 'prepare_roslyn_worker', lambda *args, **kwargs:
                        {'directory': None, 'source': 'fixture-without-build-sdk'})
    workers = tmp_path / 'atlas/semantic_providers'
    workers.mkdir(parents=True)
    for name in ('typescript_worker.cjs', 'python_worker.py'):
        (workers / name).write_text('fixture subprocess resource')
    for relative in BUILD.semantic_worker_data.__globals__['HOST_FILES']:
        identity = tmp_path / relative
        identity.parent.mkdir(parents=True, exist_ok=True)
        identity.write_text('fixture host adapter identity')
    monkeypatch.setattr(sys, 'argv', ['build.py'])
    BUILD.main()
    archive = tmp_path / 'build' / 'code_atlas' / 'PYZ-00.pyz'
    first = ZlibArchiveReader(str(archive)).extract('fixture')
    assert 'old snapshot' in first.co_consts
    source.write_text("VALUE = 'new snapshot'\n")
    os.utime(source, ns=(original_stamp, original_stamp))
    BUILD.main()
    second = ZlibArchiveReader(str(archive)).extract('fixture')
    assert 'new snapshot' in second.co_consts
    assert 'old snapshot' not in second.co_consts
    capsys.readouterr()
