"""Exercise documentation rendering with a real C# parser response, not lexical hand-built order."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from atlas.execution_path import build_execution_path


def test_documentation_uses_actual_same_line_evaluation(tmp_path):
    """C# nested calculation precedes await/response despite the outer call's earlier column."""
    node_binary = shutil.which('node')
    if not node_binary:
        pytest.skip('Node.js is required for frontend documentation verification')
    content = b'''class Handler {
    /// <param name="request">Original request.</param>
    public async Task<Result> Run(Request request) {
        if (activity != null) { activity.Tag(); }
        BeginScope();
        try { return Ok(await Calculate(request)); }
        catch (Exception error) { throw; }
    }
    public Result Calculate(Request request) { return result; }
    public Result Ok(Result value) { return value; }
}'''
    (tmp_path / 'Handler.cs').write_bytes(content)
    nodes = [{'id': identifier, 'label': label, 'kind': 'method', 'file': 'Handler.cs', 'line': line}
             for identifier, label, line in [('run', 'Run', 3), ('calc', 'Calculate', 9), ('ok', 'Ok', 10)]]
    # The old captured bytes differ; rendering this evidence must not block any call action.
    nodes[0]['commentSource'] = {'sourceKind': 'snapshot-captured-source', 'sourceHash': hashlib.sha256(b'old').hexdigest()}
    graph = {'nodes': nodes, 'edges': [{'source': 'run', 'target': target, 'relation': 'calls', 'source_location': 'L6'}
                                    for target in ['calc', 'ok']], 'metadata': {'graphUpdatedAt': '2026-10-14T12:00:00Z'}}
    data = build_execution_path(tmp_path, graph, 'run', 's')
    filename = tmp_path / 'description.json'
    filename.write_text(json.dumps(data))
    result = subprocess.run([node_binary, str(Path(__file__).with_suffix('.mjs')), str(filename)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'callbacks PASS' in result.stdout
