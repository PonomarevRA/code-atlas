"""Per-file failure isolation preserves good code and exposes missing facts to agents."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy

import pytest

from atlas.agent_views import search_nodes
from atlas.analysis_diagnostics import attach_diagnostics
from atlas.graph_context import read_graph_node
from atlas.node_description import build_node_description
from atlas.integrations.graphify.adapter import build_graph
from atlas.mcp_response_budget import compact_description

POLICY = runpy.run_path(str(Path(__file__).parents[1] / 'resources/integrations/graphify/analysis_issues.py'))


@pytest.mark.parametrize('name,content,excluded', [
    ('docs/reports/result.json', b'[' + b' ' * 1048576 + b']', True),
    ('docs/reports/result.json', b'{' + b' ' * 1048576 + b'}', False),
    ('docs/reports/tsconfig.json', b'[' + b' ' * 1048576 + b']', False),
    ('docs/reports/composer.json', b'[' + b' ' * 1048576 + b']', False),
    ('docs/reports/result.json', b'[' + b' ' * 1048576, False),
    ('source/result.json', b'[' + b' ' * 1048576 + b']', False),
])
def test_data_classification_remains_narrow(tmp_path, name, content, excluded):
    """
    Only verified artifact arrays are classified data; every refusal remains explicit partial.

    Args:
        tmp_path: Isolated pytest filesystem, unrelated to registered user data.
        name: Relative generated/config/source fixture name.
        content: Exact JSON bytes used to test the classification boundary.
        excluded: Expected verified data classification.
    """
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    issue = POLICY['file_issue'](path, tmp_path, {'error': 'json file too large to index'})
    assert bool(issue.get('excludedData')) is excluded
    assert issue['alternative'] == {'kind': 'data-map', 'file': name, 'status': 'not-built'}
    assert issue['bytes'] == len(content)
    if excluded:
        assert issue['sourceHash'] == sha256(content).hexdigest()


def test_real_provider_keeps_good_code_when_json_config_fails(tmp_path):
    """
    A genuine invalid config is not ignored or called data, and good cross-file calls survive.

    Args:
        tmp_path: Isolated pytest filesystem, unrelated to registered user data.
    """
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'a.py').write_text('from b import work\ndef run(): return work()\n')
    (root / 'b.py').write_text('def work(): return 42\n')
    (root / 'package.json').write_text('{' + ' ' * 1048576 + '}')
    graph = build_graph(root, tmp_path / 'out', chunk_max_files=1)
    diagnostics = graph['metadata']['analysisDiagnostics']
    assert diagnostics['analysisStatus'] == 'partial' and diagnostics['errorCount'] == 1
    assert diagnostics['failedFileCount'] == 1 and diagnostics['processedFileCount'] == 2
    assert not diagnostics['issues'][0].get('excludedData')
    assert any(edge['relation'] == 'calls' for edge in graph['edges'])
    found = search_nodes(graph, 'package.json')
    assert found['analysisStatus'] == 'partial'
    issue_node = next(node for node in found['nodes'] if node['kind'] == 'analysis-error')
    assert read_graph_node(graph, issue_node['id'])['node']['analysisIssue']['severity'] == 'error'
    described = build_node_description(root, graph, issue_node['id'], 'snapshot')
    assert described['sourceKind'] == 'analysis-diagnostic'
    assert described['analysisStatus'] == 'partial'
    assert not any(edge['source'] == issue_node['id'] or edge['target'] == issue_node['id'] for edge in graph['edges'])


def test_budgeted_description_retains_completeness():
    """
    Large program facts cannot remove the top-level partial warning.
    """
    result = compact_description({'analysisStatus': 'partial', 'errorCount': 5,
        'failedFileCount': 3, 'excludedDataCount': 2,
        'coverage': {'failedFileCount': 5}, 'factualSummary': 'x' * 100000}, 's', 'n', ())
    assert result['analysisStatus'] == 'partial' and result['errorCount'] == 5
    assert result['failedFileCount'] == 3 and result['excludedDataCount'] == 2


def test_real_mcp_budget_retains_warning_when_metadata_cannot_fit(monkeypatch):
    """
    Invoke registered SDK tools so both search fallback and description compaction keep counters.

    Args:
        monkeypatch: Routes the real MCP loopback transport to an in-memory HTTP fixture.
    """
    import asyncio
    import httpx
    from atlas import agent_mcp
    from test_agent_mcp_scope import tool_data
    counters = {'analysisStatus': 'partial', 'errorCount': 4, 'failedFileCount': 3,
                'excludedDataCount': 1, 'coverage': {'failedFileCount': 3, 'excludedDataCount': 1}}

    def handler(request):
        """
        Return deliberately oversized fixture metadata, without opening a network socket.

        Args:
            request: In-memory loopback HTTP request to the fixture transport.
        """
        if request.url.path.endswith('search-nodes'):
            return httpx.Response(200, json={**counters, 'snapshotId': 's', 'nodes': [],
                                           'total': 0, 'fatMetadata': 'x' * 70000})
        return httpx.Response(200, json={**counters, 'snapshotId': 's', 'nodeId': 'n',
                                       'factualSummary': 'x' * 70000})

    original = httpx.AsyncClient
    monkeypatch.setattr(agent_mcp.httpx, 'AsyncClient', lambda **options: original(**options, transport=httpx.MockTransport(handler)))

    async def scenario():
        """
        Exercise actual registered tools and inspect their bounded structured results.
        """
        server = agent_mcp.create_mcp(8877, project_id='one')
        for name, arguments in [('search_nodes', {'query': 'missing'}),
                                ('describe_node', {'snapshotId': 's', 'nodeId': 'n'})]:
            result = tool_data(await server.call_tool(name, arguments))
            assert all(result[key] == value for key, value in counters.items())
            assert len(json.dumps(result).encode()) < 64000
    asyncio.run(scenario())


def test_parse_errors_and_missing_grammar_are_safe_file_issues(tmp_path):
    """
    Partial AST and dependency error text never masquerade as valid program declarations.

    Args:
        tmp_path: Isolated pytest filesystem, unrelated to registered user data.
    """
    source = tmp_path / 'a.py'
    source.write_text('bad')
    assert POLICY['file_issue'](source, tmp_path, {'nodes': [{'id': 'partial'}], 'parse_errors': True})['code'] == 'PARSE_ERROR'
    issue = POLICY['file_issue'](source, tmp_path, {'error': 'tree-sitter-python not installed /secret/credentials'})
    assert issue['code'] == 'GRAMMAR_UNAVAILABLE'
    assert 'secret' not in json.dumps(issue)


def test_large_data_validation_budget_and_unrelated_error_stay_failed(tmp_path):
    """
    A data classification budget is a refusal to classify, not permission to hide an error.

    Args:
        tmp_path: Isolated generated artifact root.
    """
    path = tmp_path / 'artifacts' / 'result.json'
    path.parent.mkdir()
    path.write_bytes(b'[' + b' ' * POLICY['DATA_VALIDATION_BYTES'] + b']')
    issue = POLICY['file_issue'](path, tmp_path, {'error': 'json file too large to index'})
    assert not issue.get('excludedData')
    other = POLICY['file_issue'](path, tmp_path, {'error': 'unrelated failure'})
    assert other['code'] == 'FILE_EXTRACTION_FAILED' and 'alternative' not in other


def test_diagnostic_namespace_collision_is_fatal():
    """
    A provider ID is never overwritten by the diagnostic namespace.
    """
    graph = {'nodes': [{'id': 'collision'}]}
    with pytest.raises(ValueError, match='collision'):
        attach_diagnostics(graph, {'issues': [{'nodeId': 'collision'}]})


def test_unhandled_language_is_visible_while_other_language_survives(tmp_path):
    """
    An extension counted as code without an extractor is explicitly unsupported, never silently complete.

    Args:
        tmp_path: Isolated source and output roots unrelated to registered user maps.
    """
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'good.py').write_text('def work(): return 42\n')
    (root / 'unsupported.ets').write_text('struct Component {}\n')
    graph = build_graph(root, tmp_path / 'out')
    assert graph['metadata']['analysisDiagnostics']['analysisStatus'] == 'partial'
    assert graph['metadata']['analysisDiagnostics']['issues'][0]['code'] == 'UNSUPPORTED_SOURCE'
    assert any(node.get('file') == 'good.py' for node in graph['nodes'])


def test_same_output_reuse_preserves_partial_and_rejects_old_failure_cache(tmp_path):
    """
    A warm output retries failed sources and cannot turn their missing facts into complete coverage.

    Args:
        tmp_path: Isolated source/cache directories; no registered map is touched.
    """
    import graphify.cache as cache
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'good.py').write_text('def work(): return 42\n')
    bad = root / 'package.json'
    bad.write_text('{' + ' ' * 1048576 + '}')
    output = tmp_path / 'out'
    first = build_graph(root, output, chunk_max_files=1)
    second = build_graph(root, output, chunk_max_files=1)
    assert first['metadata']['analysisDiagnostics'] == second['metadata']['analysisDiagnostics']
    assert second['metadata']['analysisDiagnostics']['analysisStatus'] == 'partial'
    assert second['metadata']['analysisDiagnostics']['sourceFileCount'] == 2
    assert second['metadata']['chunkAnalysis']['fileExtractorCalls']['chunks'] == 1
    # Simulate an interrupted/older job that persisted the pre-fix sentinel.
    cache.save_cached(bad, {'nodes': [], 'edges': [], 'skipped': 'atlas-analysis-error'},
                      root, cache_root=output)
    third = build_graph(root, output, chunk_max_files=1)
    assert third['metadata']['analysisDiagnostics'] == first['metadata']['analysisDiagnostics']
    assert sum(node['kind'] == 'analysis-error' for node in third['nodes']) == 1
    assert third['metadata']['chunkAnalysis']['fileExtractorCalls']['chunks'] >= 1
    cache.save_cached(root / 'good.py', {'nodes': [{'id': 'bad-cached-ast'}], 'edges': [],
                                       'parse_errors': True}, root, cache_root=output)
    fourth = build_graph(root, output, chunk_max_files=1)
    assert fourth['metadata']['analysisDiagnostics'] == first['metadata']['analysisDiagnostics']
    assert all(node['id'] != 'bad-cached-ast' for node in fourth['nodes'])


def test_all_local_failures_publish_diagnostic_only_partial(tmp_path):
    """
    A healthy runtime with no valid program facts can still publish a useful diagnostic-only map.

    Args:
        tmp_path: Isolated failing source and output, unrelated to registered maps.
    """
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'package.json').write_text('{' + ' ' * 1048576 + '}')
    graph = build_graph(root, tmp_path / 'out')
    assert graph['metadata']['analysisDiagnostics']['analysisStatus'] == 'partial'
    assert graph['metadata']['analysisDiagnostics']['processedFileCount'] == 0
    assert graph['metadata']['programNodeCount'] == 0
    assert len(graph['nodes']) == 1 and graph['nodes'][0]['kind'] == 'analysis-error'
    assert graph['edges'] == []
