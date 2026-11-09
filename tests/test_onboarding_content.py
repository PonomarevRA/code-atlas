"""Onboarding provenance compares exact bounded reads without changing captured facts."""

from copy import deepcopy
import hashlib

import pytest

from atlas.execution_path import build_execution_path
from atlas.node_description import build_node_description
from atlas.source_provenance import source_provenance
from atlas.source_viewer import SourceError, read_node_source


def source_case(tmp_path, content=b'class Handler { public int Run(int x) { return x; } }'):
    """Create one known C# declaration and a captured full-byte hash."""
    filename = tmp_path / 'Handler.cs'
    filename.write_bytes(content)
    node = {'id': 'run', 'kind': 'method', 'label': 'Run', 'file': 'Handler.cs', 'line': 1,
            'commentSource': {'sourceKind': 'snapshot-captured-source', 'file': 'Handler.cs',
                              'sourceHash': hashlib.sha256(content).hexdigest()}}
    return filename, {'nodes': [node], 'edges': [], 'metadata': {'graphUpdatedAt': '2026-10-14T12:00:00+00:00'}}


@pytest.mark.parametrize('bom', [b'', b'\xef\xbb\xbf'])
def test_exact_byte_provenance_and_single_read(tmp_path, monkeypatch, bom):
    """BOM files compare byte-for-byte while parser hashes retain their original text basis."""
    from atlas import execution_path_source
    content = bom + b'class Handler { public int Run(int x) { return x; } }'
    _, graph = source_case(tmp_path, content)
    before = deepcopy(graph)
    original = execution_path_source.read_node_source
    reads = []
    def counted(*args, **kwargs):
        """Fail any accidental extra read introduced by comparison."""
        reads.append(args[1]['id'])
        return original(*args, **kwargs)
    monkeypatch.setattr(execution_path_source, 'read_node_source', counted)
    description = build_node_description(tmp_path, graph, 'run', 'snapshot')
    provenance = description['sourceProvenance']
    assert provenance['status'] == 'match'
    assert provenance['currentSourceHash'] == hashlib.sha256(content).hexdigest()
    assert description['sourceHash'] == hashlib.sha256(content.removeprefix(b'\xef\xbb\xbf')).hexdigest()
    assert provenance['currentReadAt']
    assert provenance['snapshotCreatedAt'] == graph['metadata']['graphUpdatedAt']
    assert reads == ['run'] and graph == before
    path = build_execution_path(tmp_path, graph, 'run', 'snapshot')
    assert path['sourceProvenance'] == path['steps'][0]['sourceProvenance']


def test_changed_and_missing_hashes_remain_distinct(tmp_path):
    """A real byte change is visible; absent captured evidence never means a current snapshot."""
    filename, graph = source_case(tmp_path)
    filename.write_bytes(b'class Handler { public int Run(int x) { return x + 1; } }')
    result = build_node_description(tmp_path, graph, 'run', 'snapshot')
    assert result['sourceProvenance']['status'] == 'changed'
    graph['nodes'][0].pop('commentSource')
    result = build_node_description(tmp_path, graph, 'run', 'snapshot')
    assert result['sourceProvenance']['status'] == 'unknown'
    assert result['sourceProvenance']['reason'] == 'captured-hash-missing'
    assert result['sourceProvenance']['currentReadAt']


@pytest.mark.parametrize('content', [None, b'\xff\xfeinvalid', b'\x00binary'])
def test_unavailable_sources_never_claim_current_read(tmp_path, content):
    """Missing, non-UTF-8 and binary files preserve captured evidence without a current hash."""
    filename, graph = source_case(tmp_path)
    if content is None:
        filename.unlink()
    else:
        filename.write_bytes(content)
    result = build_node_description(tmp_path, graph, 'run', 'snapshot')
    facts = result['sourceProvenance']
    assert facts['status'] == 'unknown' and facts['reason'] == 'current-source-unavailable'
    assert facts['currentSourceHash'] is None and facts['currentReadAt'] is None
    assert facts['capturedSourceHash']
    with pytest.raises(SourceError):
        read_node_source(tmp_path, graph['nodes'][0], 'snapshot')


def test_captured_only_and_conflicts_are_unknown():
    """Captured-only results and contradictory snapshot facts cannot masquerade as checked files."""
    captured = 'a' * 64
    node = {'commentSource': {'sourceKind': 'snapshot-captured-source', 'sourceHash': captured}}
    description = {'sourceKind': 'snapshot-captured-source', 'sourceHash': captured}
    facts = source_provenance({'metadata': {'attributeGraph': {'capturedAt': 'analyzer-date'}}}, node, description, 's')
    assert facts['status'] == 'unknown' and facts['currentSourceHash'] is None
    assert facts['snapshotCreatedAt'] is None
    node['complexity'] = {'sourceKind': 'snapshot-captured-source', 'sourceHash': 'b' * 64}
    facts = source_provenance({}, node, {'sourceByteHash': captured, 'sourceReadAt': 'now'}, 's')
    assert facts['status'] == 'unknown' and facts['reason'] == 'captured-hash-conflict'


def test_total_style_evaluation_and_source_contract_are_preserved(tmp_path):
    """Activity guards remain separate; inner calculation precedes await, response and return."""
    content = b'''class Handler {
    /// <summary>Produce a response.</summary>
    /// <param name="request">Original request.</param>
    /// <returns>A response result.</returns>
    public async Task<Result> Run(Request request) {
        if (activity != null) { activity.Tag(); }
        BeginScope();
        try { return Ok(await CalculateAsync(request)); }
        catch (Exception error) { throw; }
    }
}'''
    _, graph = source_case(tmp_path, content)
    result = build_node_description(tmp_path, graph, 'run', 'snapshot')
    steps = result['methodFlow']['steps']
    assert [step['controlKind'] for step in steps if step['kind'] == 'branch'] == ['if', 'try']
    assert steps[1]['kind'] == 'call' and steps[1]['label'] == 'BeginScope()'
    normal = steps[2]['arms'][0]['steps']
    assert [step['kind'] for step in normal] == ['call', 'await', 'call', 'return']
    assert normal[0]['label'] == 'CalculateAsync()' and normal[2]['label'].startswith('Ok(')
    calculation = next(event for event in result['events'] if event.get('callee') == 'CalculateAsync')
    assert calculation['sourceExpression'] == 'CalculateAsync(request)'
    assert result['parameters'] == [{'name': 'request', 'type': 'Request'}]
    assert result['returnType'] == 'Task<Result>'
    assert result['documentation']['params'][0]['text'] == 'Original request.'
