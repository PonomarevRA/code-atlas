"""Standalone acceptance fixture выполняет настоящий импорт каждого базового формата."""
import pytest
from types import SimpleNamespace
from pathlib import Path
from importlib.machinery import EXTENSION_SUFFIXES

from atlas.native_documents_check import check_native_documents, _native_modules, NATIVE_MODULES


def test_native_check_requires_compiled_modules_for_release():
    """Source-mode PASS не должен подменять проверку native release."""
    with pytest.raises(RuntimeError, match='manifest'):
        check_native_documents()


def test_check_fixtures_extract_all_formats_without_user_data():
    """Все семь реальных fixtures проходят process worker и последующее чтение индекса."""
    result = check_native_documents(require_compiled=False)
    assert result['status'] == 'PASS'
    assert result['documentCount'] == 7
    assert set(result['formats']) == {'guide.md', 'values.yaml', 'events.jsonl', 'guide.docx', 'data.xlsx', 'slides.pptx', 'guide.pdf'}
    assert set(result['checkedContent']) == set(result['formats'])


def test_incomplete_or_external_native_artifacts_cannot_pass_release(tmp_path, monkeypatch):
    """Частичный manifest и подмена DLL вне бандла не считаются standalone release."""
    import atlas.native_documents_check as checks
    entries = []
    origins = {}
    for name in sorted(NATIVE_MODULES):
        filename = Path('atlas/documents') / (name + EXTENSION_SUFFIXES[0])
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture-native-artifact')
        module = 'atlas.documents.' + name
        origins[module] = str(path)
        entries.append({'module': module, 'file': str(filename)})
    monkeypatch.setattr(checks.util, 'find_spec', lambda name: SimpleNamespace(origin=origins[name]))
    assert len(_native_modules({'schemaVersion': 1, 'extensions': entries}, tmp_path)) == len(NATIVE_MODULES)
    with pytest.raises(RuntimeError, match='Incomplete'):
        _native_modules({'schemaVersion': 1, 'extensions': entries[:1]}, tmp_path)
    origins[entries[0]['module']] = str(tmp_path.parent / ('external' + EXTENSION_SUFFIXES[0]))
    with pytest.raises(RuntimeError, match='bundled'):
        _native_modules({'schemaVersion': 1, 'extensions': entries}, tmp_path)
