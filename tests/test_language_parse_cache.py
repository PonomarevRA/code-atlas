"""Request-local syntax reuse keeps budgets, declaration identities and fresh source evidence."""
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages.parsed_source import parsed_tree

SAMPLES = json.loads((Path(__file__).parent / 'fixtures/flow/provider-grid.json').read_text())


@pytest.mark.parametrize('language', list(SAMPLES))
def test_real_provider_parses_one_file_once_but_rebuilds_mutable_facts(tmp_path, monkeypatch, language):
    """All real providers reuse syntax in one analysis; events and graph identities remain independent."""
    import tree_sitter
    parser_class, python_parse, calls = tree_sitter.Parser, ast.parse, []

    class CountingParser:
        """Count actual Tree-sitter parse calls without replacing a grammar or its output."""
        def __init__(self, *args, **kwargs):
            self.inner = parser_class(*args, **kwargs)

        def parse(self, content):
            calls.append('tree-sitter')
            return self.inner.parse(content)

    def parse_python(*args, **kwargs):
        calls.append('python')
        return python_parse(*args, **kwargs)

    monkeypatch.setattr(tree_sitter, 'Parser', CountingParser)
    monkeypatch.setattr(ast, 'parse', parse_python)
    suffix, text, line = SAMPLES[language]
    filename = language + suffix
    (tmp_path / filename).write_text(text)
    node = {'id': 'raw-run', 'file': filename, 'label': 'run()', 'kind': 'method', 'line': line}
    original = deepcopy(node)
    analysis = SourceAnalysis(tmp_path, 'snapshot', 2, 100_000, 20)
    first = analysis.describe(node)
    first['events'].clear()
    first['methodFlow']['steps'].clear()
    second = analysis.describe(node)
    assert second['sourceStatus'] == 'parsed', second['unresolved']
    assert any(event.get('callee') == 'work' for event in second['events'])
    assert second['methodFlow']['steps']
    assert len(calls) == 1, (language, calls)
    assert node == original and (tmp_path / filename).read_text() == text
    assert '_flow_parse_cache' not in second
    if language != 'csharp':
        assert len(analysis.files[filename]['_flow_parse_cache']['trees']) == 1


@pytest.mark.parametrize('language', list(SAMPLES))
def test_new_analysis_at_same_path_reads_changed_source(tmp_path, language):
    """No process cache may let one request's syntax mask later working-tree changes."""
    suffix, text, line = SAMPLES[language]
    filename = language + suffix
    path = tmp_path / filename
    node = {'id': 'raw-run', 'file': filename, 'label': 'run()', 'kind': 'method', 'line': line}
    path.write_text(text)
    first = SourceAnalysis(tmp_path, 's', 2, 100_000, 20).describe(node)
    path.write_text(text.replace('work(', 'next('))
    second = SourceAnalysis(tmp_path, 's', 2, 100_000, 20).describe(node)
    assert first['sourceHash'] != second['sourceHash']
    assert any(event.get('callee') == 'next' for event in second['events'])
    assert not any(event.get('callee') == 'work' for event in second['events'])


def test_exact_text_and_tsx_grammar_separate_cached_trees():
    """A changed DTO text or grammar mode cannot reuse incompatible syntax."""
    calls = []
    source = {'text': 'same', '_flow_parse_cache': {}}

    def parse(content):
        calls.append(content)
        return ast.parse("pass")

    typescript = parsed_tree(source, 'typescript', parse)
    assert parsed_tree(source, 'typescript', parse) is typescript
    assert parsed_tree(source, 'tsx', parse) is not typescript
    source['text'] = 'changed'
    assert parsed_tree(source, 'typescript', parse) is not typescript
    assert calls == [b'same', b'same', b'changed']
    assert list(source['_flow_parse_cache']['trees']) == ['typescript']


def test_direct_callers_are_not_mutated_and_failed_parse_is_not_cached():
    """Only host-owned DTOs retain syntax; parser failures remain retryable."""
    source = {'text': 'input'}
    parsed_tree(source, 'python', lambda data: ast.parse("pass"))
    assert source == {'text': 'input'}
    source['_flow_parse_cache'] = {}

    def broken(data):
        raise ValueError('grammar unavailable')

    with pytest.raises(ValueError):
        parsed_tree(source, 'python', broken)
    assert parsed_tree(source, 'python', lambda data: ast.parse('pass')).body[0].__class__ is ast.Pass


def test_host_byte_budget_prevents_retained_tree(tmp_path):
    """Source rejected by the shared byte budget never allocates a retained parse cache."""
    (tmp_path / 'code.py').write_text('def run():\n    work()\n')
    analysis = SourceAnalysis(tmp_path, 's', 2, 1, 20)
    result = analysis.describe({'id': 'run', 'file': 'code.py', 'label': 'run()', 'line': 1})
    assert result['sourceStatus'] == 'unavailable' and analysis.truncation['bytes']
    assert analysis.files == {'code.py': None}


def test_oversized_tree_is_not_retained():
    """The request source-byte budget is not mistaken for an AST memory budget."""
    source = {'text': 'pass\n' * 20_001, '_flow_parse_cache': {}}
    tree = parsed_tree(source, 'python', ast.parse)
    assert len(tree.body) == 20_001
    assert source['_flow_parse_cache']['trees'] == {}


def test_host_fact_read_skips_presentation_tokenization(tmp_path, monkeypatch):
    """Language flow needs source coordinates, never a syntax-highlighted HTML payload."""
    import atlas.source_syntax as syntax

    def unexpected_highlight(*args, **kwargs):
        pytest.fail('Flow source reads must not tokenize presentation syntax')

    monkeypatch.setattr(syntax, 'highlight_source', unexpected_highlight)
    (tmp_path / 'code.py').write_text('def run():\n    work()\n')
    analysis = SourceAnalysis(tmp_path, 's', 2, 100_000, 20)
    result = analysis.describe({'id': 'run', 'file': 'code.py', 'label': 'run()', 'line': 1})
    assert result['sourceStatus'] == 'parsed'
    assert 'syntax' not in analysis.files['code.py']


def test_actual_typescript_and_tsx_modes_do_not_share_incompatible_syntax():
    """Identical source in different grammar modes preserves each parser's own failure boundary."""
    from atlas.flow_languages.typescript import analyze
    source = {'text': 'function run(x: number) { return <div>{work(x)}</div>; }',
              'file': 'code.ts', '_flow_parse_cache': {}}
    node = {'id': 'run', 'label': 'run()', 'line': 1}
    typescript = analyze(source, node, max_events=20)
    source['file'] = 'code.tsx'
    tsx = analyze(source, node, max_events=20)
    assert typescript['sourceStatus'] != 'parsed'
    assert tsx['sourceStatus'] == 'parsed'
    assert set(source['_flow_parse_cache']['trees']) == {'typescript', 'tsx'}


@pytest.mark.parametrize('language', [name for name in SAMPLES if name != 'csharp'])
def test_reusing_syntax_never_reuses_a_previous_event_budget(language):
    """A clipped first description cannot poison a later description with a larger event budget."""
    from atlas.flow_languages.registry import language_adapter
    suffix, text, line = SAMPLES[language]
    source = {'text': text, 'file': 'code' + suffix, '_flow_parse_cache': {}}
    node = {'id': 'raw-run', 'label': 'run()', 'kind': 'method', 'line': line}
    adapter = language_adapter(source['file'])
    empty = adapter(source, node, max_events=0)
    full = adapter(source, node, max_events=20)
    assert empty['events'] == []
    assert any(event.get('callee') == 'work' for event in full['events'])
