"""C# obeys the host's source and syntax budgets without publishing a partial declaration index."""
from atlas.execution_path_source import SourceAnalysis


def test_csharp_ast_budget_discards_prefix_and_does_not_poison_other_files(tmp_path, monkeypatch):
    """An oversized file remains unavailable; another file in the same request still parses."""
    import atlas.execution_path_source as source
    monkeypatch.setattr(source, 'MAX_AST_NODES', 25)
    (tmp_path / 'large.cs').write_text('class Large { ' + ' '.join(f'void M{i}() {{ Target(); }}' for i in range(20)) + ' }')
    (tmp_path / 'small.cs').write_text('class Small { void Run() {} }')
    analysis = SourceAnalysis(tmp_path, 'snapshot', 3, 100_000, 20)
    large = {'id': 'large', 'file': 'large.cs', 'label': 'M0()', 'line': 1}
    result = analysis.describe(large)
    assert result['sourceStatus'] == 'unavailable'
    assert result['declarationTruncated']
    assert result['events'] == []
    assert 'AST C#' in ' '.join(result['unresolved'])
    assert analysis.files['large.cs'] is None
    assert analysis.truncation['ast']
    assert analysis.describe(large)['declarationTruncated']
    small = analysis.describe({'id': 'small', 'file': 'small.cs', 'label': 'Run()', 'line': 1})
    assert small['sourceStatus'] == 'parsed'
    assert not small['declarationTruncated']


def test_csharp_fact_analysis_does_not_highlight_source(tmp_path, monkeypatch):
    """The diagram analyzer uses its own AST and must not build viewer syntax tokens."""
    import atlas.source_syntax as syntax

    def unexpected_highlight(*args, **kwargs):
        raise AssertionError('C# analysis must not tokenize for display')

    monkeypatch.setattr(syntax, 'highlight_source', unexpected_highlight)
    (tmp_path / 'small.cs').write_text('class Small { int Run() { return 1; } }')
    analysis = SourceAnalysis(tmp_path, 'snapshot', 1, 100_000, 20)
    result = analysis.describe({'id': 'small', 'file': 'small.cs', 'label': 'Run()', 'line': 1})
    assert result['sourceStatus'] == 'parsed'
    assert result['kind'] == 'method'
    assert 'Run' in result['signature']
    assert result['sourceHash']
