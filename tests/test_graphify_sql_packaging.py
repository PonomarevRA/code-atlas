"""
SQL packaging preflight checks the pinned native grammar before producing an offline application.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('atlas_packaging_graphify_grammars', ROOT / 'packaging/graphify_grammars.py')
grammars = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grammars)


def install_mock(monkeypatch, *, version='0.3.11', failure=None, parse_error=False):
    """
    Supply deterministic installed-module contracts without downloading or installing any grammar.

    Args:
        monkeypatch: Pytest patch scope restored after the test.
        version: Installed distribution version reported to the strict preflight.
        failure: Optional exception representing a missing/broken native binding.
        parse_error: Grammar loads but rejects the representative SQL schema.
    Returns:
        Captured parser inputs, proving the preflight actually invokes parsing.
    """
    parsed = []
    class Parser:
        """
        Minimal parser protocol fixture with visible real-call capture.
        """
        def __init__(self, language):
            """
            Require the language instance constructed from the grammar export.

            Args:
                language: Test Language wrapper result.
            """
            assert language == 'language-instance'
        def parse(self, source):
            """
            Capture the schema bytes and return a controlled grammar tree result.

            Args:
                source: Bounded in-memory SQL schema, never a user file.
            Returns:
                Tree protocol with explicit syntax-error and named-child metadata.
            """
            parsed.append(source)
            return SimpleNamespace(root_node=SimpleNamespace(has_error=parse_error, named_child_count=1))
    def load(name):
        """
        Model native module import, including failures that find_spec alone would miss.

        Args:
            name: Known grammar or runtime import name.
        Returns:
            Protocol-compatible installed module fixture.
        Raises:
            Exception: Selected synthetic native binding/import failure.
        """
        if failure:
            raise failure
        if name == 'tree_sitter_sql':
            return SimpleNamespace(language=lambda: 'capsule')
        assert name == 'tree_sitter'
        return SimpleNamespace(Language=lambda capsule: 'language-instance', Parser=Parser)
    monkeypatch.setattr(grammars.metadata, 'version', lambda name: version)
    monkeypatch.setattr(grammars, 'import_module', load)
    return parsed


def test_pin_import_and_actual_parse_are_required(monkeypatch):
    """
    Successful preflight loads the capsule and parses rather than checking package names alone.

    Args:
        monkeypatch: Private installed-module fixture scope.
    """
    parsed = install_mock(monkeypatch)
    assert grammars.check_sql_grammar() == {'distribution': 'tree-sitter-sql', 'version': '0.3.11'}
    assert parsed == [b'CREATE TABLE atlas_sql_smoke (id INTEGER PRIMARY KEY);']


@pytest.mark.parametrize('failure', [ModuleNotFoundError('missing'), ImportError('wrong ABI'), OSError('native library load')])
def test_missing_or_broken_native_grammar_stops_build(monkeypatch, failure):
    """
    A present distribution with missing or incompatible bindings cannot pass the build gate.

    Args:
        monkeypatch: Installed-module fixture scope.
        failure: Distinct grammar load failure mode.
    """
    install_mock(monkeypatch, failure=failure)
    with pytest.raises(RuntimeError, match='Synchronize requirements.lock'):
        grammars.check_sql_grammar()


@pytest.mark.parametrize('version,parse_error', [('0.3.10', False), ('0.3.11', True)])
def test_unpinned_or_nonfunctional_grammar_is_rejected(monkeypatch, version, parse_error):
    """
    Exact dependency pin and usable SQL syntax are independent necessary conditions.

    Args:
        monkeypatch: Installed-module fixture scope.
        version: Reported distribution version.
        parse_error: Whether parsing returns an error-bearing tree.
    """
    install_mock(monkeypatch, version=version, parse_error=parse_error)
    with pytest.raises(RuntimeError, match='tree-sitter-sql==0.3.11'):
        grammars.check_sql_grammar()


def test_required_sql_pin_and_preflight_cover_build_and_direct_spec():
    """
    Both build entry points retain preflight before expensive compilation/Analysis.
    """
    assert 'tree-sitter-sql==0.3.11' in (ROOT / 'requirements.lock').read_text().splitlines()
    build = (ROOT / 'packaging/build.py').read_text()
    spec = (ROOT / 'packaging/code_atlas.spec').read_text()
    assert build.index('sql_grammar = check_sql_grammar()') < build.index('native_manifest = build_overlay(')
    assert spec.index('check_sql_grammar()') < spec.index('analysis = Analysis(')
    assert "'tree-sitter-sql'" in spec and "module.name.startswith('tree_sitter_')" in spec


def test_actual_installed_sql_grammar_when_available():
    """
    Exercise real native grammar in environments where the locked dependency is installed.
    """
    if importlib.util.find_spec('tree_sitter_sql') is None:
        pytest.skip('Locked SQL grammar absent from this test interpreter; target build probe owned separately')
    assert grammars.check_sql_grammar()['version'] == '0.3.11'


def test_real_graphify_sql_extraction_when_grammar_available():
    """
    Exercise the actual vendor extractor on an in-memory schema, without creating source/data files.
    """
    if importlib.util.find_spec('tree_sitter_sql') is None:
        pytest.skip('Target-runtime Graphify SQL qualification runs where the locked grammar is installed')
    from graphify.extractors.sql import extract_sql
    result = extract_sql(Path('atlas_build_probe.sql'), content=(
        'CREATE TABLE customers (id INTEGER PRIMARY KEY);\n'
        'CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id));\n'))
    assert not result.get('error')
    labels = {node.get('label') for node in result['nodes']}
    assert {'customers', 'orders'}.issubset(labels)
    assert len(result['edges']) >= 2


def test_absent_distribution_stops_before_any_native_import(monkeypatch):
    """
    Missing locked metadata fails immediately even before attempting a native module load.

    Args:
        monkeypatch: Private metadata/import fixture scope.
    """
    def missing(name):
        """
        Model a build environment without the required pinned distribution.

        Args:
            name: Distribution requested by the preflight.
        Raises:
            PackageNotFoundError: No matching installed package metadata exists.
        """
        raise grammars.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(grammars.metadata, 'version', missing)
    monkeypatch.setattr(grammars, 'import_module', lambda name: pytest.fail('Native import must not run'))
    with pytest.raises(RuntimeError, match='requirements.lock'):
        grammars.check_sql_grammar()
