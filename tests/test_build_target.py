"""A target label never substitutes for a matching native Python process."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('atlas_build_target', ROOT / 'packaging/build_target.py')
TARGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TARGET)


@pytest.mark.parametrize(('raw', 'expected'), [
    ('macos-arm64', 'darwin-arm64'), ('darwin-aarch64', 'darwin-arm64'),
    ('linux-amd64', 'linux-x86_64'), ('windows-x64', 'win32-x86_64'),
    ('WIN32-ARM64', 'win32-arm64'), ('osx-x86-64', 'darwin-x86_64'),
])
def test_target_aliases(raw, expected):
    """Equivalent platform names share the runtime-pack directory contract."""
    assert TARGET.normalize_target(raw) == expected


def test_emulated_windows_process_uses_python_architecture():
    """An x64 Python on ARM Windows must produce an x64-labelled artifact."""
    assert TARGET.process_target(system='win32', machine='ARM64', python_platform='win-amd64', pointer_bits=64) == 'win32-x86_64'
    assert TARGET.process_target(system='win32', machine='ARM64', python_platform='win-arm64', pointer_bits=64) == 'win32-arm64'
    with pytest.raises(ValueError, match='64-bit'):
        TARGET.process_target(system='win32', machine='AMD64', python_platform='win32', pointer_bits=32)


def test_foreign_target_is_rejected_before_build_starts():
    """A CLI mismatch exits before invoking Cython/PyInstaller or copying data."""
    actual = TARGET.process_target()
    foreign = 'linux-arm64' if actual != 'linux-arm64' else 'linux-x86_64'
    result = subprocess.run([sys.executable, str(ROOT / 'packaging/build.py'), '--target', foreign],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Cannot build' in result.stderr
    assert TARGET.select_target('macos-aarch64', actual='darwin-arm64') == 'darwin-arm64'


def test_packaging_includes_only_the_matching_lnav_pack(tmp_path, monkeypatch):
    """An Intel certified pack must never be copied into an ARM artifact."""
    from atlas import lnav_runtime
    checked = []

    def checked_capability(directory):
        """Record the exact explicit pack passed to the authoritative runtime verifier."""
        checked.append(directory)
        return {'available': True}

    monkeypatch.setattr(lnav_runtime, 'capability', checked_capability)
    root = tmp_path / 'resources/lnav/darwin-x86_64'
    root.mkdir(parents=True)
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps({'platform': 'darwin-x86_64', 'certified': True}))
    assert TARGET.lnav_pack_data(tmp_path, 'macos-arm64') == []
    assert checked == []
    assert TARGET.lnav_pack_data(tmp_path, 'macos-x64') == [(str(root), 'resources/lnav/darwin-x86_64')]
    assert checked == [root]
    manifest.write_text(json.dumps({'platform': 'darwin-arm64', 'certified': True}))
    with pytest.raises(ValueError, match='not certified'):
        TARGET.lnav_pack_data(tmp_path, 'macos-x64')


def test_ci_declares_distinct_native_architectures():
    """Validate YAML and prevent implicit latest-runner architecture assumptions."""
    workflow = yaml.load((ROOT / '.github/workflows/native-build.yml').read_text(), Loader=yaml.BaseLoader)
    matrix = workflow['jobs']['build']['strategy']['matrix']['include']
    assert {item['target'] for item in matrix} == {
        'darwin-x86_64', 'darwin-arm64', 'linux-x86_64', 'linux-arm64', 'win32-x86_64'}
    assert 'inputs.windows_arm64' in workflow['jobs']['windows-arm64']['if']
    gitlab = yaml.safe_load((ROOT / '.gitlab-ci.yml').read_text())
    assert gitlab['linux-arm64']['variables']['ATLAS_BUILD_TARGET'] == 'linux-arm64'


def test_textual_lnav_certification_is_not_native_proof(tmp_path):
    """A forged matching label without a verified binary closure fails before packaging."""
    target = TARGET.process_target()
    directory = tmp_path / 'resources/lnav' / target
    directory.mkdir(parents=True)
    (directory / 'manifest.json').write_text(json.dumps({'platform': target, 'certified': True}))
    (directory / 'lnav').write_text('not a native executable')
    with pytest.raises(ValueError, match='verification failed'):
        TARGET.lnav_pack_data(tmp_path, target)
