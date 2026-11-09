"""Build-only contract checks for the standalone document extension overlay."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('atlas_native_document_packaging', ROOT / 'packaging/native_documents.py')
PACK = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(ROOT / 'packaging'))
try:
    SPEC.loader.exec_module(PACK)
finally:
    sys.path.pop(0)


def test_overlay_compiles_without_changing_source(tmp_path):
    """Actually compile/import a small extension; detect accidental source fallback."""
    pytest.importorskip('Cython')
    source = tmp_path / 'source'
    package = source / 'atlas/documents'
    package.mkdir(parents=True)
    (source / 'atlas/__init__.py').write_text('', encoding='utf-8')
    (package / '__init__.py').write_text('', encoding='utf-8')
    original = 'from pathlib import Path\n\ndef doubled(value: int):\n    """Retain Python duck typing despite the annotation."""\n    return value + value\n'
    module = package / 'example.py'
    module.write_text(original, encoding='utf-8')
    work = tmp_path / 'build'
    manifest = PACK.build_overlay(source, work)
    assert module.read_text(encoding='utf-8') == original
    assert not (work / 'overlay/atlas/documents/example.py').exists()
    entry = manifest['extensions'][0]
    binary = work / 'overlay' / entry['file']
    assert entry['sha256'] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert entry['sourceSha256'] == hashlib.sha256(module.read_bytes()).hexdigest()
    assert 'pathlib' in manifest['hiddenImports']
    code = 'import atlas.documents.example as m; assert m.doubled("x") == "xx"; print(m.__file__)'
    result = subprocess.run([sys.executable, '-I', '-c',
                             'import sys; sys.path.insert(0, ' + repr(str(work / 'overlay')) + ');' + code],
                            check=True, text=True, capture_output=True)
    assert manifest['extensionSuffix'] in result.stdout
    assert json.loads((work / 'native-documents-manifest.json').read_text()) == manifest


def test_source_tree_cannot_be_selected_as_build_directory(tmp_path):
    """Prevent the overlay cleanup from ever deleting source ancestors."""
    root = tmp_path / 'source'
    root.mkdir()
    with pytest.raises(ValueError, match='must not contain'):
        PACK.build_overlay(root, root)
    with pytest.raises(ValueError, match='must not contain'):
        PACK.build_overlay(root, tmp_path)
    with pytest.raises(ValueError, match='must not contain'):
        PACK.build_overlay(root, root / 'atlas/generated')


def test_release_directives_preserve_python_semantics():
    """Release optimization must not disable runtime checks or reinterpret hints."""
    assert PACK.DIRECTIVES['annotation_typing'] is False
    assert PACK.DIRECTIVES['boundscheck'] is True
    assert PACK.DIRECTIVES['wraparound'] is True
    assert PACK.DIRECTIVES['initializedcheck'] is True
    assert PACK.compile_flags() in (['-O2'], ['/O2'])
    assert len(PACK.source_modules(ROOT)) >= 15


def test_native_manifest_uses_process_abi_on_arm_windows(monkeypatch):
    """An emulated x64 Python must never label its compiled pyd files ARM64."""
    monkeypatch.setattr(PACK, 'process_target', lambda: 'win32-x86_64')
    monkeypatch.setattr(PACK.platform, 'machine', lambda: 'ARM64')
    assert PACK.target_metadata() == {'target': 'win32-x86_64', 'platform': 'win32',
                                      'architecture': 'x86_64', 'processMachine': 'ARM64'}
