"""Usage is an optional additive diagnostics contract with the existing loopback policy."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.technical_logs.metrics import register_diagnostics_routes
from atlas.usage_metrics import UsageMetrics, UsageMiddleware
from test_technical_logs import journal


def test_direct_json_and_prometheus_export_share_counts_and_do_not_observe_polls(journal):
    """Both read-only projections preserve counters and exclude monitoring requests themselves."""
    metrics = UsageMetrics()
    with metrics.operation('semantic-csharp', 'semantic_analyze'):
        pass
    app = FastAPI()
    app.add_middleware(UsageMiddleware, metrics=metrics)
    register_diagnostics_routes(app, (journal,), usage=metrics)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1',1)) as client:
        first = metrics.snapshot()
        response = client.get('/api/diagnostics/v1/metrics')
        assert response.json()['usage'] == first
        exposition = client.get('/api/diagnostics/v1/prometheus')
        assert exposition.status_code == 200
        assert 'text/plain; version=0.0.4' in exposition.headers['content-type']
        assert exposition.headers['cache-control'] == 'no-store'
        assert 'atlas_mod_calls_total{operation="semantic_analyze",mod="semantic-csharp",outcome="success"} 1' in exposition.text
        assert metrics.snapshot() == first
        assert client.get('/api/diagnostics/v1/prometheus?token=PRIVATE').status_code == 400
        assert client.get('/api/diagnostics/v1/prometheus',headers={'Origin':'https://evil.example'}).status_code == 403
        assert client.post('/api/diagnostics/v1/prometheus').status_code == 405


def test_usage_snapshot_failure_keeps_resource_diagnostics_available(journal, monkeypatch):
    """An optional registry failure does not discard independent resource measurements or leak errors."""
    metrics = UsageMetrics()
    def broken():
        raise RuntimeError('PRIVATE failure')
    monkeypatch.setattr(metrics, 'snapshot', broken)
    app = FastAPI()
    register_diagnostics_routes(app, (journal,), usage=metrics)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1',1)) as client:
        response = client.get('/api/diagnostics/v1/metrics')
        assert response.status_code == 200 and 'process' in response.json() and 'usage' not in response.json()
        failure = client.get('/api/diagnostics/v1/prometheus')
        assert failure.status_code == 503 and 'PRIVATE' not in failure.text
