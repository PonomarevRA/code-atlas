"""Host source/retention boundaries, independent of the pure optimizers' own oracles."""

import asyncio
from copy import deepcopy
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from atlas.analysis_contexts import AnalysisContexts
from atlas.map_maintenance import MapMaintenance
from atlas.module_analysis_api import register_module_analysis_routes
from atlas.module_analysis_service import ModuleAnalysisService
from atlas.module_proposal_api import register_module_proposal_routes
from atlas.module_proposal_service import ModuleProposalService
from atlas.module_proposal_mcp import register_module_proposal_tools
from mcp.server.fastmcp import FastMCP
from atlas.store import StoreError


class Store:
    """A controlled publisher; tests can change only the currently published revision."""
    def __init__(self):
        self.sid = 's1'
        self.maintenance = MapMaintenance()
        self.graph = {'metadata': {'analysisStatus': 'complete'}, 'nodes': [
            {'id': name, 'file': folder+'/code.py', 'kind': 'function'}
            for name, folder in [('a', 'one'), ('b', 'two'), ('c', 'three')]],
            'edges': [{'source': 'a', 'target': 'b', 'relation': 'imports'},
                      {'source': 'b', 'target': 'a', 'relation': 'imports'}]}

    def assert_snapshot(self, project, snapshot):
        """Reject another project and stale revision before context/fact access."""
        if project != 'p':
            raise StoreError('Unknown project', 404)
        if snapshot != self.sid:
            raise StoreError('Changed snapshot', 409)

    def comparison_snapshot(self, project):
        """Expose shared input so any accidental mutation is observable."""
        return None, self.graph


@pytest.fixture
def feature():
    """Use real pure engines behind strict API DTOs and actual shared context ownership."""
    app, store, contexts = FastAPI(), Store(), AnalysisContexts()
    analysis = ModuleAnalysisService(store, concurrency=1)
    service = ModuleProposalService(store, contexts, slots=analysis.slots)
    register_module_analysis_routes(app, analysis)
    register_module_proposal_routes(app, service)
    @app.exception_handler(StoreError)
    async def store_error(request, error):
        return JSONResponse({'error': str(error)}, status_code=error.status)
    try:
        with TestClient(app) as client:
            yield client, store, contexts, service, analysis
    finally:
        contexts.close()


def test_search_uses_host_source_equal_caps_and_no_mutation(feature):
    client, store, contexts, _, analysis = feature
    before = deepcopy(store.graph)
    response = client.post('/api/projects/p/module-search', json={
        'snapshotId': 's1', 'evaluationsPerMethod': 8, 'maxProposals': 4, 'seed': 7})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['readOnly'] and body['snapshotId'] == 's1'
    assert body['search']['source']['projectionHash'] == body['projection']['projectionHash']
    assert body['search']['budgets']['evaluationsPerMethod'] == 8
    assert all(item['evaluations'] <= 8 for item in body['search']['methods'].values())
    known = {item['id'] for item in body['projection']['units']}
    for item in body['search']['proposals']:
        assert {node for group in item['partition'] for node in group} == known
    assert store.graph == before and not contexts.contexts
    assert store.maintenance.requests == 0 and analysis.slots.acquire(blocking=False)
    analysis.slots.release()


def test_small_scope_constraint_defaults_remain_host_dependent_over_http(feature):
    """An omitted maximum is actual N, not the DTO schema maximum of128."""
    client, _, _, _, _ = feature
    path = '/api/projects/p/module-search'
    base = client.post(path, json={'snapshotId':'s1','evaluationsPerMethod':4}).json()
    ids = [unit['id'] for unit in base['projection']['units']]
    partition = [ids[:2], ids[2:]]
    for constraints in [{}, {'mustLink': [[ids[0],ids[1]]]}]:
        response = client.post(path, json={'snapshotId':'s1','evaluationsPerMethod':4,
            'currentPartition':partition, 'constraints':constraints})
        assert response.status_code == 200, response.text
        assert response.json()['search']['constraints']['maxGroups'] == len(ids)
    explicit = client.post(path, json={'snapshotId':'s1','constraints':{'maxGroups':128}})
    assert explicit.status_code == 400, 'an explicitly invalid user cap must not be silently clamped'


def test_mcp_constraints_preserve_unset_fields_before_http_validation(feature):
    """Actual FastMCP argument parsing retains omission through both DTO adapters."""
    client, _, _, _, _ = feature
    server = FastMCP('module-proposal-constraint-fixture')
    async def transport(method, path, body):
        response = client.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()
    register_module_proposal_tools(server, transport, lambda project: project or 'p')
    async def scenario():
        result = await server.call_tool('search_code_module_partitions', {
            'projectId':'p','snapshotId':'s1','constraints':{},'evaluationsPerMethod':4})
        payload = result if isinstance(result, dict) else json.loads(result[0].text)
        assert payload['search']['constraints']['maxGroups'] == 3
    asyncio.run(scenario())


@pytest.mark.parametrize('extra', [{'graph': {}}, {'sourceFacts': []}, {'seed': True},
    {'evaluationsPerMethod': True}, {'maxProposals': 17}, {'constraints': {'unknown': 1}},
    {'constraints': {'minGroups': True}}])
def test_caller_graph_and_invalid_budgets_are_rejected(feature, extra):
    client, _, contexts, _, _ = feature
    response = client.post('/api/projects/p/module-search', json={'snapshotId': 's1', **extra})
    assert response.status_code == 422 and not contexts.contexts


def test_incremental_uses_retained_old_state_and_new_host_graph(feature):
    client, store, contexts, _, analysis = feature
    first = client.post('/api/projects/p/module-updates', json={'snapshotId': 's1', 'maxEvaluations': 64})
    assert first.status_code == 200, first.text
    old = first.json()
    assert len(contexts.contexts) == 1 and 'state' not in old
    store.sid = 's2'
    store.graph['edges'].append({'source': 'b', 'target': 'c', 'relation': 'imports'})
    next_result = client.post('/api/projects/p/module-updates', json={
        'snapshotId': 's2', 'stateRef': old['stateRef'], 'maxEvaluations': 64})
    assert next_result.status_code == 200, next_result.text
    result = next_result.json()
    assert result['update']['previousSnapshotId'] == 's1' and result['snapshotId'] == 's2'
    assert result['algorithm'] == 'atlas-incremental-directed/v1'
    assert result['update']['hierarchy']['fullRebuildVerified'] is True
    assert result['update']['search']['evaluationsUsed'] <= 64
    assert result['stateRef']['contextId'] != old['stateRef']['contextId']
    assert len(contexts.contexts) == 2 and store.maintenance.requests == 0
    assert analysis.slots.acquire(blocking=False)
    analysis.slots.release()


def test_foreign_or_forged_old_state_never_becomes_source(feature):
    client, _, contexts, _, _ = feature
    old = client.post('/api/projects/p/module-updates', json={'snapshotId': 's1'}).json()['stateRef']
    assert client.post('/api/projects/other/module-updates', json={'snapshotId': 's1', 'stateRef': old}).status_code == 404
    forged = {**old, 'settings': 'forged'}
    assert client.post('/api/projects/p/module-updates', json={'snapshotId': 's1', 'stateRef': forged}).status_code == 409
    unknown = {**old, 'contextId': '0'*32}
    assert client.post('/api/projects/p/module-updates', json={'snapshotId': 's1', 'stateRef': unknown}).status_code == 410
    assert len(contexts.contexts) == 1


def test_unretained_state_does_not_return_a_dead_handle(feature):
    client, _, contexts, _, _ = feature
    contexts.max_bytes = 1
    response = client.post('/api/projects/p/module-updates', json={'snapshotId': 's1'})
    assert response.status_code == 429 and not contexts.contexts


def test_source_change_before_state_publication_releases_context(feature, monkeypatch):
    client, store, contexts, _, _ = feature
    import atlas.module_proposal_service as source
    original = source.initial_state
    def changed(*args, **kwargs):
        """Publish a different revision after pure computation, before host retention."""
        result = original(*args, **kwargs)
        store.sid = 'replaced'
        return result
    monkeypatch.setattr(source, 'initial_state', changed)
    response = client.post('/api/projects/p/module-updates', json={'snapshotId': 's1'})
    assert response.status_code == 409 and not contexts.contexts
    assert store.maintenance.requests == 0


def test_all_module_requests_share_the_existing_admission_limit(feature):
    client, _, contexts, _, analysis = feature
    assert analysis.slots.acquire(blocking=False)
    try:
        for operation in ['module-search', 'module-updates']:
            assert client.post('/api/projects/p/'+operation, json={'snapshotId': 's1'}).status_code == 429
    finally:
        analysis.slots.release()
    assert not contexts.contexts
