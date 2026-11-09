"""Load CLI bounds protect the local machine before fixtures or output paths are created."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def load_runner():
    """Load the standalone diagnostic without starting workloads or touching a data store."""
    path = Path(__file__).parents[1] / 'tools/performance/chunk_load.py'
    spec = spec_from_file_location('chunk_load_contract', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('case', ['provider:python:0', 'provider:csharp:1001',
                                 'provider:unknown:50', 'storage:50001', 'storage:-1',
                                 'http:1000:0', 'http:10001:2', 'http:1:101',
                                 '../../unsafe', 'http:2:2/../../other'])
def test_unbounded_or_path_bearing_workloads_are_rejected_before_run(case):
    """Malformed specs cannot allocate huge corpora or escape the chosen output directory."""
    runner = load_runner()
    with pytest.raises(ValueError, match='out-of-budget'):
        runner.parse_spec(case)


def test_record_comparison_detects_duplicate_evidence_loss():
    """Count equality cannot hide a missing duplicate edge with otherwise identical facts."""
    runner = load_runner()
    edge = {'source': 'a', 'target': 'b', 'relation': 'calls', 'line': 4}
    assert runner.record_counter([edge, edge]) != runner.record_counter([edge])


@pytest.mark.parametrize('before,after', [({'a.py': 'old'}, {'a.py': 'new'}),
                                        ({}, {'new.py': 'hash'}), ({'gone.py': 'hash'}, {})])
def test_source_drift_invalidates_successful_measurement(before, after):
    """Changed, added or removed owning modules cannot retain a passing receipt."""
    runner = load_runner()
    original = {'status': 'PASS', 'spec': 'storage:1000'}
    result = runner.qualify_sources(original, before, after)
    assert result['status'] == 'INVALID' and not result['sourcesStableDuringCase']
    assert result['changedSources'] and original['status'] == 'PASS'


def test_valid_small_http_load_accepts_a_complete_single_page():
    """The real HTTP/MCP path succeeds when one edge legitimately has no continuation."""
    runner = load_runner()
    result = runner.http_case(edge_count=1)
    assert result['httpPageLatency']['samples'] == result['completedTraversals'] == 8
    assert result['mcpFourToolCycleLatency']['samples'] == 10
