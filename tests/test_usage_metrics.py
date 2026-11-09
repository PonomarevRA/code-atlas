"""Core metrics observe real callback boundaries; no private source/request data leaves the registry."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
import pytest
from test_semantic_runtime import setup_runtime
from fastapi import FastAPI
from fastapi.testclient import TestClient
from atlas.usage_metrics import UsageMetrics, UsageMiddleware
from atlas.usage_contract import MAX_RECORDS, MAX_BYTES, validate_usage, prometheus_lines

def test_concurrent_calls_errors_snapshot_and_inflight():
    """Every completed callback counts once, concurrent work remains visible and copies are independent."""
    metrics = UsageMetrics()

    def run(index):
        with metrics.operation('semantic-csharp/roslyn', 'semantic_analyze'):
            if index % 5 == 0:
                raise ValueError('SECRET original payload')
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run, i) for i in range(200)]
        for future in futures:
            try:
                future.result()
            except ValueError:
                pass
    value = validate_usage(metrics.snapshot())
    row = value['records'][0]
    assert row['count'] == 200 and row['outcomes']['error'] == 40 and (row['inFlight'] == 0)
    assert sum(row['buckets']) == 200
    assert 'SECRET' not in json.dumps(value)
    row['outcomes']['success'] = 0
    assert metrics.snapshot()['records'][0]['outcomes']['success'] == 160
    measurement = metrics.begin('semantic-csharp', 'semantic_probe')
    assert metrics.snapshot()['records'][1]['inFlight'] == 1
    measurement.finish('unavailable')
    measurement.finish('error')
    assert metrics.snapshot()['records'][1]['count'] == 1

def test_labels_rows_bytes_and_discovery_separated():
    """Catalog reads are not observed; cache/rule passes have different families and bounded rows."""
    metrics = UsageMetrics()
    assert not metrics.snapshot()['records']
    metrics.cache_hit('semantic-csharp/roslyn')
    metrics.rule_pass('dotnet-tests')
    for index in range(MAX_RECORDS + 30):
        with metrics.operation('mod-' + str(index), 'grouping'):
            pass
    assert metrics.begin('token=private', 'grouping') is None
    value = validate_usage(metrics.snapshot())
    assert len(value['records']) == MAX_RECORDS and value['droppedObservations'] == 32
    assert len(json.dumps(value).encode()) < MAX_BYTES
    text = '\n'.join(prometheus_lines(value))
    assert 'atlas_mod_cache_total' in text and 'atlas_mod_rule_passes_total' in text
    assert 'token=private' not in text
    assert '# TYPE atlas_mod_calls_total counter' in text

def test_http_mcp_private_data_polling_errors_and_cancellation():
    """Matched route categories and allowlisted declared clients survive; user values never do."""
    app = FastAPI()
    metrics = UsageMetrics()
    app.add_middleware(UsageMiddleware, metrics=metrics)

    @app.post('/api/projects/{project_id}/nodes')
    def endpoint(project_id: str):
        return {'ok': True}

    @app.get('/api/diagnostics/v1/metrics')
    def polling():
        return {}
    with TestClient(app) as client:
        client.post('/api/projects/PRIVATE-GUID/nodes?token=PRIVATE', json={'secret': 'PRIVATE'}, headers={'X-Atlas-MCP-Session': 'a' * 32, 'X-Atlas-MCP-Client': 'codex'})
        client.get('/api/diagnostics/v1/metrics')
        client.get('/api/missing/PRIVATE')
    value = validate_usage(metrics.snapshot())
    raw = json.dumps(value)
    assert 'PRIVATE' not in raw and 'a' * 32 not in raw
    assert sum((row['count'] for row in value['records'])) == 2
    assert any((row['client'] == 'codex' and row['operation'] == 'node' for row in value['records']))
    assert any((row['outcomes']['error'] == 1 for row in value['records']))

    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(UsageMiddleware(cancelled, metrics)({'type': 'http', 'path': '/api/foo', 'headers': []}, None, None))
    assert metrics.snapshot()['httpInFlight'] == 0
    assert sum((row['outcomes']['cancelled'] for row in metrics.snapshot()['records'])) == 1

def test_real_runtime_probe_analysis_and_cache_boundaries(tmp_path):
    """Use the production runtime with a synthetic provider, preserving actual callback counts."""
    from test_semantic_runtime import Provider, Clock, HASH
    from atlas.semantic_runtime import SemanticRuntime
    from atlas.semantic_models import SemanticContext
    asset = tmp_path / 'engine'
    asset.write_text('synthetic')
    provider = Provider(asset)
    provider.provider_id = 'semantic-test/provider'
    metrics = UsageMetrics()
    runtime = SemanticRuntime(tmp_path / 'cache', {provider.provider_id: provider}, clock=Clock(), usage=metrics)
    context = SemanticContext('snapshot', 'workspace', str(tmp_path), 'target', HASH, {'a.cs': HASH}, HASH)
    try:
        runtime.on_use(context, [provider.provider_id])
        assert runtime.wait_for_idle()
        for _ in range(3):
            runtime.on_use(context, [provider.provider_id])
        assert runtime.submit_analysis(context, provider.provider_id, {}).result(timeout=3) is not None
        rows = validate_usage(metrics.snapshot())['records']
        assert provider.probes == 1 and provider.analyses == 1
        assert next((r for r in rows if r['kind'] == 'mod' and r['operation'] == 'semantic_probe'))['count'] == 1
        assert next((r for r in rows if r['kind'] == 'cache'))['count'] == 3
        assert next((r for r in rows if r['operation'] == 'semantic_analyze'))['count'] == 1
    finally:
        runtime.close()

def test_real_grouping_counts_execution_not_catalog(tmp_path):
    from test_mods import package, active
    from atlas.mod_store import ModStore
    from atlas.mod_grouping import resolve, provider_id
    import networkx as nx
    package(tmp_path / 'state/mods')
    metrics = UsageMetrics()
    store = ModStore(tmp_path / 'state', tmp_path / 'empty', usage=metrics)
    item = active(store)
    callback = resolve(store, provider_id(item, item['manifest']['grouping'][0]))
    assert not metrics.snapshot()['records']
    graph = nx.Graph()
    graph.add_node('one', kind='method')
    assert callback(graph) == [{'one'}]
    row = metrics.snapshot()['records'][0]
    assert row['mod'] == 'example' and row['count'] == 1 and (row['operation'] == 'grouping')

@pytest.mark.parametrize('mutation', ['version', 'private', 'nan', 'duplicate', 'buckets'])
def test_contract_refuses_poisoned_or_incompatible_export(mutation):
    metrics = UsageMetrics()
    with metrics.operation('semantic-test', 'grouping'):
        pass
    value = metrics.snapshot()
    if mutation == 'version':
        value['schemaVersion'] = 'unknown'
    elif mutation == 'private':
        value['records'][0]['query'] = 'PRIVATE'
    elif mutation == 'nan':
        value['records'][0]['seconds'] = float('nan')
    elif mutation == 'duplicate':
        value['records'].append(value['records'][0])
    else:
        value['records'][0]['buckets'][0] += 5
    with pytest.raises(ValueError):
        validate_usage(value)

@pytest.mark.parametrize('status,expected', [('failed', 'error'), ('cancelled', 'cancelled'), ('unsupported', 'unavailable'), ('partial', 'success')])
def test_provider_result_status_controls_metrics(setup_runtime, status, expected):
    """Returned failure DTOs count as failure even when the callback raises no exception."""
    from dataclasses import replace
    runtime, context, provider, clock = setup_runtime
    metrics = UsageMetrics()
    runtime.usage = metrics
    original = provider.analyze
    provider.analyze = lambda *args: replace(original(*args), status=status)
    runtime.on_use(context, [provider.provider_id])
    assert runtime.wait_for_idle()
    result = runtime.submit_analysis(context, provider.provider_id, {}).result(timeout=3)
    assert result.status == status
    row = next((r for r in metrics.snapshot()['records'] if r['operation'] == 'semantic_analyze'))
    assert row['outcomes'][expected] == 1
    assert row['outcomes']['success'] == int(expected == 'success')

@pytest.mark.parametrize('fault', ['begin', 'finish', 'http_change', 'cache_hit'])
def test_optional_telemetry_failure_preserves_provider_and_http_results(setup_runtime, monkeypatch, fault):
    """Telemetry exceptions cannot prevent callback execution or overwrite a successful HTTP response."""

    def broken(*args, **kwargs):
        raise RuntimeError('PRIVATE telemetry error')
    metrics = UsageMetrics()
    monkeypatch.setattr(metrics, fault, broken)
    runtime, context, provider, clock = setup_runtime
    runtime.usage = metrics
    runtime.on_use(context, [provider.provider_id])
    assert runtime.wait_for_idle()
    runtime.on_use(context, [provider.provider_id])
    assert runtime.submit_analysis(context, provider.provider_id, {}).result(timeout=3) is not None
    assert provider.probes == provider.analyses == 1
    app = FastAPI()
    app.add_middleware(UsageMiddleware, metrics=metrics)

    @app.get('/api/projects')
    def result():
        return {'ok': True}
    with TestClient(app) as client:
        response = client.get('/api/projects')
        assert response.status_code == 200 and response.json() == {'ok': True}

    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(UsageMiddleware(cancelled, metrics)({'type': 'http', 'path': '/api/foo', 'headers': []}, None, None))

@pytest.mark.parametrize('mutate', [lambda value: value.update(startedAt=10 ** 1000), lambda value: value['records'][0].update(kind=[])])
def test_malformed_usage_always_has_controlled_validation_error(mutate):
    """Untrusted JSON-shaped data fails as ValueError rather than overflowing or hashing arbitrary objects."""
    metrics = UsageMetrics()
    with metrics.operation('test-provider', 'grouping'):
        pass
    value = metrics.snapshot()
    mutate(value)
    with pytest.raises(ValueError):
        validate_usage(value)
