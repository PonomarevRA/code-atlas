"""Frozen subprocess paths contain small bridges, never language runtime/engine distributions."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('atlas_semantic_packaging', ROOT / 'packaging/semantic_engines.py')
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


def test_physical_worker_resources_match_frozen_adapter_paths(tmp_path):
    """An external user interpreter/Node can read the actual worker resource rather than a PYZ entry."""
    from atlas.semantic_providers import python, typescript
    data = packaging.semantic_worker_data(ROOT)
    assert {Path(path) for path, _ in data} == {
        python.WORKER, typescript.WORKER, *(ROOT / relative for relative in packaging.HOST_FILES)}
    for filename, destination in data:
        target = tmp_path / destination / Path(filename).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(filename, target)
        assert target.read_bytes() == Path(filename).read_bytes()
    manifest = packaging.semantic_worker_manifest(data)
    assert manifest['engineDistributionsBundled'] is False
    assert manifest['totalBytes'] < 128 * 1024
    assert all(len(item['sha256']) == 64 for item in manifest['files'])


def test_bridge_packaging_never_copies_neighboring_compilers(tmp_path):
    """Even a directory containing SDK DLLs exports only the exact two-file bridge allowlist."""
    for name in packaging.ROSLYN_FILES:
        (tmp_path / name).write_text('fixture')
    (tmp_path / packaging.ROSLYN_FILES[1]).write_text(json.dumps({'runtimeOptions': {
        'tfm': 'net8.0', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '8.0.0'}}}))
    (tmp_path / 'Microsoft.CodeAnalysis.dll').write_bytes(b'NEVER_PACKAGE_ENGINE')
    data = packaging.semantic_worker_data(ROOT, tmp_path)
    names = {Path(path).name for path, _ in data}
    assert 'Microsoft.CodeAnalysis.dll' not in names
    assert set(packaging.ROSLYN_FILES) <= names
    assert all(destination == 'resources/semantic-csharp' for path, destination in data if path.startswith(str(tmp_path)))


def test_missing_build_sdk_keeps_optional_profile_without_invoking_installer(tmp_path, monkeypatch):
    """A machine without a build SDK can still package Atlas's baseline and other conditional profiles."""
    monkeypatch.setattr(packaging.shutil, 'which', lambda _: None)
    monkeypatch.setattr(packaging.Path, 'is_file', lambda _: False)
    result = packaging.prepare_roslyn_worker(tmp_path)
    assert result['directory'] is None and 'no-existing-build-sdk8' in result['source']


def test_invalid_explicit_bridge_fails_instead_of_selecting_another_path(tmp_path):
    """An explicit prebuilt choice is validated rather than silently replaced by a local compiler."""
    with pytest.raises(ValueError, match='small Roslyn bridge'):
        packaging.prepare_roslyn_worker(ROOT, prebuilt=tmp_path)



def test_frozen_clangd_probe_reads_shipped_host_identities(tmp_path, monkeypatch):
    """A PYZ module's synthetic __file__ has matching physical probe inputs in the bundle."""
    from hashlib import sha256
    from threading import Event
    from atlas import semantic_lsp_providers
    from atlas.semantic_models import SemanticContext
    for filename, destination in packaging.semantic_worker_data(ROOT):
        target = tmp_path / 'bundle' / destination / Path(filename).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(filename, target)
    source = tmp_path / 'project'
    source.mkdir()
    text = 'int entry() { return 1; }\n'
    (source / 'main.cpp').write_text(text)
    (source / 'compile_commands.json').write_text(json.dumps([{'directory': str(source),
        'file': 'main.cpp', 'arguments': ['clang++', '-c', 'main.cpp']}]))
    context = SemanticContext('snapshot', 'workspace', str(source), 'cpp', 'a' * 64,
                              {'main.cpp': sha256(text.encode()).hexdigest()}, 'b' * 64)
    provider = semantic_lsp_providers.ClangdProvider('cpp')
    baseline = provider.probe(context, Event())
    if baseline.availability.status != 'available':
        pytest.skip('No compatible existing clangd for frozen resource qualification')
    monkeypatch.setattr(semantic_lsp_providers, '__file__',
                        str(tmp_path / 'bundle/atlas/semantic_lsp_providers.py'))
    outcome = provider.probe(context, Event())
    assert outcome.availability.status == 'available'
    assert outcome.assets[-2:] == (
        tmp_path / 'bundle/atlas/semantic_lsp_providers.py', tmp_path / 'bundle/atlas/semantic_lsp.py')
    assert all(path.is_file() for path in outcome.assets)
    manifest = packaging.semantic_worker_manifest(packaging.semantic_worker_data(ROOT))
    assert manifest['hostIdentityBytes'] > 0
    assert manifest['hostIdentityBytes'] + manifest['workerPayloadBytes'] == manifest['totalBytes']
