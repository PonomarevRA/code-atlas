"""Private checkout HTTP/proxy contracts, with real stores and isolated source fixtures."""

import asyncio
import json
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from atlas.checkout_maps import CheckoutMaps
from atlas.checkout_maps_api import register_checkout_routes
from atlas.checkout_maps_mcp import CHECKOUT_TOOLS, register_checkout_tools
from atlas.server import create_app
from atlas.store import StoreError
from test_checkout_maps import Clocks, FakeStore
from test_grouping_cache import legacy_store
from test_jobs import fake_build


@pytest.fixture
def private_api(tmp_path, monkeypatch):
    """Publish an actual private snapshot through a controlled extraction provider.

    The provider fixture does not execute Graphify. Real ProjectStore readers,
    HTTP validation, source descriptions and lease/TTL state remain in use.
    """
    from atlas import store
    monkeypatch.setattr(store, 'build_graph', fake_build)
    source, data = tmp_path / 'checkout', tmp_path / 'app'
    source.mkdir(); data.mkdir()
    original = 'def first():\n    """Return the fixture value."""\n    return 1\n'
    (source / 'service.py').write_text(original)
    canonical = data / 'projects.json'
    canonical.write_text('{"preserve":"canonical registry"}')
    clocks = Clocks()
    manager = CheckoutMaps(data, clock=lambda: clocks.monotonic, wall_clock=lambda: clocks.wall)
    app = FastAPI()
    register_checkout_routes(app, manager)

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        """Use the same typed store failure boundary as the stable application."""
        return JSONResponse({'error': str(error)}, status_code=error.status)

    try:
        with TestClient(app, base_url='http://127.0.0.1') as client:
            created = client.post('/api/checkout-sessions', json={'checkout': str(source), 'ownerKey': 'test.private'})
            assert created.status_code == 202, created.text
            sid = created.json()['sessionId']
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                status = client.get('/api/checkout-sessions/'+sid).json()
                if status['project']['status'] in {'completed', 'failed'}:
                    break
                time.sleep(.01)
            assert status['project']['status'] == 'completed', status
            snapshot = status['project']['snapshotId']
            yield client, manager, clocks, source, canonical, sid, snapshot
    finally:
        manager.close()


@pytest.mark.parametrize('damage', ['corrupt-index', 'linked-index'])
def test_unsafe_optional_namespace_preserves_canonical_app_startup(tmp_path, damage):
    """Corrupt optional state returns503 rather than breaking or silently emptying Atlas."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    data, registry = state.data_dir, state.registry
    state.close()
    protected = {path: path.read_bytes() for path in (registry, graph_path)}
    root = data / 'maps/temporary-checkouts'; root.mkdir(parents=True)
    index = root / 'index.json'
    if damage == 'corrupt-index':
        index.write_text('{invalid namespace')
    else:
        victim = tmp_path / 'unowned-index.json'; victim.write_text('{"keep":"unowned"}')
        index.symlink_to(victim)
    before = index.read_bytes()
    app = create_app(data)
    app.state.references.start_initial_indexing = lambda: None
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/api/health').status_code == 200
        projects = client.get('/api/projects')
        assert projects.status_code == 200
        assert project_id in {row['id'] for row in projects.json()['projects']}
        assert client.get(f'/api/projects/{project_id}/graph').status_code == 200
        unavailable = client.get('/api/checkout-sessions')
        assert unavailable.status_code == 503
        assert unavailable.json()['code'] == 'checkout_maps_unavailable'
        assert 'sessions' not in unavailable.json()
    assert index.read_bytes() == before
    assert index.is_symlink() == (damage == 'linked-index')
    for path, content in protected.items():
        assert path.read_bytes() == content


@pytest.mark.parametrize('field', ['createdAtEpoch', 'expiresAtEpoch'])
@pytest.mark.parametrize('value', [1e100, -1e100])
def test_bad_utc_namespace_returns503_without_breaking_canonical_app(tmp_path, field, value):
    """CK1: malformed persisted dates become an unavailable optional feature, not raw500."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    data, registry = state.data_dir, state.registry
    state.close()
    protected = {path: path.read_bytes() for path in (registry, graph_path)}
    source = tmp_path / 'temporary-source'; source.mkdir(); program = source / 'keep.py'
    program.write_text('def keep():\n    return 1\n')
    original_source = program.read_bytes()
    temporary = CheckoutMaps(data, store_factory=FakeStore)
    sid = temporary.ensure(source, 'fixture.owner')['sessionId']
    temporary.entries[sid].store.complete()
    temporary.close()
    records = json.loads(temporary.index.read_text())
    records['sessions'][0][field] = value
    temporary.index.write_text(json.dumps(records))
    corrupt_bytes = temporary.index.read_bytes()
    private_files = {path: path.read_bytes() for path in temporary.root.rglob('*') if path.is_file()}
    app = create_app(data)
    app.state.references.start_initial_indexing = lambda: None
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/projects').status_code == 200
        assert client.get(f'/api/projects/{project_id}/graph').status_code == 200
        for path in ('/api/checkout-sessions', '/api/checkout-sessions/'+sid):
            unavailable = client.get(path)
            assert unavailable.status_code == 503
            assert unavailable.json()['code'] == 'checkout_maps_unavailable'
            assert 'sessions' not in unavailable.json()
    assert temporary.index.read_bytes() == corrupt_bytes
    assert all(path.read_bytes() == content for path, content in private_files.items())
    assert all(path.read_bytes() == content for path, content in protected.items())
    assert program.read_bytes() == original_source


def test_strict_invalid_stale_and_oversized_reads_do_not_extend_ttl(private_api, monkeypatch):
    """Validation, snapshot rejection and final response budgets precede useful-read touch."""
    client, manager, clocks, _, _, sid, snapshot = private_api
    base = '/api/checkout-sessions/'+sid
    before = client.get(base).json()['expiresAt']
    clocks.advance(60)
    requests = [({'snapshotId': 'stale', 'query': 'First', 'limit': 1}, 409),
                ({'snapshotId': snapshot, 'limit': True}, 422),
                ({'snapshotId': snapshot, 'limit': 1, 'sourceFacts': []}, 422)]
    for body, expected in requests:
        assert client.post(base+'/search', json=body).status_code == expected
        assert client.get(base).json()['expiresAt'] == before
    assert client.post(base+'/describe', json={'snapshotId': 'stale', 'nodeId': 'first'}).status_code == 409
    assert client.post(base+'/health', json={'snapshotId': snapshot, 'maxMethods': True}).status_code == 422
    assert client.get(base).json()['expiresAt'] == before
    entry = manager.entries[sid]
    original = entry.store.search_project_nodes

    def oversized(*args):
        """Model a legitimate large source response after the actual snapshot reader."""
        result = original(*args)
        result['padding'] = 'x' * 64_000
        return result

    monkeypatch.setattr(entry.store, 'search_project_nodes', oversized)
    assert client.post(base+'/search', json={'snapshotId': snapshot, 'query': 'First', 'limit': 1}).status_code == 413
    assert client.get(base).json()['expiresAt'] == before
    monkeypatch.setattr(entry.store, 'search_project_nodes', original)
    assert client.post(base+'/search', json={'snapshotId': snapshot, 'query': 'First', 'limit': 1}).status_code == 200
    assert client.get(base).json()['expiresAt'] != before


def test_useful_private_reads_and_rebuild_preserve_source_and_canonical_scope(private_api):
    """Search/describe/health operate on the private owner and never register a permanent map."""
    client, manager, clocks, source, canonical, sid, snapshot = private_api
    original_source, original_registry = (source / 'service.py').read_bytes(), canonical.read_bytes()
    base = '/api/checkout-sessions/'+sid
    clocks.advance(20)
    found = client.post(base+'/search', json={'snapshotId': snapshot, 'query': 'First', 'limit': 10})
    assert found.status_code == 200, found.text
    facts = found.json()
    assert facts['temporary'] and facts['checkoutSessionId'] == sid and facts['snapshotId'] == snapshot
    # Source enrichment adds other raw facts; search order is not a relevance guarantee.
    node = next(item for item in facts['nodes'] if item['id'] == 'first')
    described = client.post(base+'/describe', json={'snapshotId': snapshot, 'nodeId': node['id']})
    assert described.status_code == 200, described.text
    assert described.json()['nodeId'] == 'first' and described.json()['temporary']
    health = client.post(base+'/health', json={'snapshotId': snapshot, 'sourceMode': 'working-tree',
                                             'files': ['service.py'], 'maxMethods': 1})
    assert health.status_code == 200, health.text
    assert health.json()['checkoutSessionId'] == sid and health.json()['behaviourVerification'] == 'not-performed'
    health_records = list(manager.entries[sid].path.rglob('*.json'))
    assert health_records and all(path.is_relative_to(manager.root / sid) for path in health_records)
    rebuilt = client.post(base+'/rebuild', json={})
    assert rebuilt.status_code == 202 and rebuilt.json()['sessionId'] == sid
    assert (source / 'service.py').read_bytes() == original_source
    assert canonical.read_bytes() == original_registry


def test_private_health_forecast_and_comparison_never_write_draft_into_checkout(private_api):
    """Exercise successful private baseline/plan/compare, retaining unverified behaviour."""
    client, _, _, source, canonical, sid, snapshot = private_api
    before_source, before_registry = (source / 'service.py').read_bytes(), canonical.read_bytes()
    base = '/api/checkout-sessions/'+sid
    assessed = client.post(base+'/health', json={'snapshotId': snapshot, 'sourceMode': 'working-tree',
                                               'files': ['service.py'], 'maxMethods': 1})
    assert assessed.status_code == 200, assessed.text
    baseline = assessed.json()['assessmentId']
    draft = 'def first():\n    """Return a proposed fixture value."""\n    return 2\n'
    planned = client.post(base+'/plan', json={'baselineAssessmentId': baseline, 'intent': 'Forecast a draft',
                                            'drafts': [{'file': 'service.py', 'content': draft}]})
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    assert plan['temporary'] and plan['checkoutSessionId'] == sid and plan['baselineAssessmentId'] == baseline
    compared = client.post(base+'/compare', json={'beforeAssessmentId': baseline, 'afterAssessmentId': baseline,
                                                'planId': plan['planId']})
    assert compared.status_code == 200, compared.text
    assert compared.json()['temporary'] and compared.json()['checkoutSessionId'] == sid
    assert (source / 'service.py').read_bytes() == before_source
    assert canonical.read_bytes() == before_registry


class ToolRegistry:
    """Capture actual MCP proxy bodies; SDK/schema/stdio proof is separate root evidence."""

    def __init__(self):
        self.tools, self.annotations = {}, {}

    def tool(self, **kwargs):
        """Retain registration declarations without substituting any proxy implementation."""
        def register(function):
            self.tools[function.__name__] = function
            self.annotations[function.__name__] = kwargs.get('annotations')
            return function
        return register


def tool_arguments():
    """Provide valid scoped arguments so binding failures reach the shared transport guard."""
    sid, identity = 'a'*32, 'b'*32
    return {'ensure_checkout_map': {'checkout': '/declared/checkout', 'ownerKey': 'test.task'},
            'ensure_checkout_map_with_reclaim': {'checkout': '/declared/checkout', 'ownerKey': 'test.task'},
            'list_checkout_maps': {}, 'get_checkout_map': {'sessionId': sid},
            'rebuild_checkout_map': {'sessionId': sid}, 'finish_checkout_map': {'sessionId': sid},
            'search_checkout_nodes': {'sessionId': sid, 'snapshotId': 's'},
            'search_checkout_file': {'sessionId': sid, 'path': 'service.py'},
            'describe_checkout_node': {'sessionId': sid, 'snapshotId': 's', 'nodeId': 'n'},
            'get_checkout_code_health': {'sessionId': sid, 'snapshotId': 's'},
            'plan_checkout_code_changes': {'sessionId': sid, 'baselineAssessmentId': identity,
                'intent': 'Forecast a draft', 'drafts': [{'file': 'service.py', 'content': 'def first():\n    return 2\n'}]},
            'compare_checkout_code_health': {'sessionId': sid, 'beforeAssessmentId': identity, 'afterAssessmentId': identity}}


def test_mcp_rebuild_sends_empty_json_object_and_exposes_write_semantics():
    """POST{} is required by the host JSON boundary even for an argument-free rebuild body."""
    registry, calls = ToolRegistry(), []

    async def request(method, path, body=None):
        """Capture the exact method/path/body before any simulated transport response."""
        calls.append((method, path, body))
        return {'sessionId': 'a'*32, 'temporary': True}

    register_checkout_tools(registry, request)
    asyncio.run(registry.tools['rebuild_checkout_map'](sessionId='a'*32))
    assert calls == [('POST', '/api/checkout-sessions/'+('a'*32)+'/rebuild', {})]
    assert registry.annotations['rebuild_checkout_map'].readOnlyHint is False
    assert registry.annotations['finish_checkout_map'].destructiveHint is True
    assert registry.annotations['search_checkout_nodes'].readOnlyHint is True


def test_reclaim_proxy_and_http_admit_the_same_task(private_api):
    """The destructive MCP retry reclaims a full slot through the actual HTTP route."""
    client, manager, clocks, source, canonical, old, _ = private_api
    manager.capacity = 1
    clocks.advance(1200)
    registry = ToolRegistry()
    payload = {'checkout': str(source), 'ownerKey': 'new.task', 'label': None}
    overflow = client.post('/api/checkout-sessions', json=payload)
    assert overflow.status_code == 429
    assert 'ensure_checkout_map_with_reclaim' in overflow.json()['error']

    async def request(method, path, body=None):
        """Dispatch the proxy's fixed loopback request through TestClient."""
        response = client.request(method, path, json=body)
        assert response.is_success, response.text
        return response.json()

    register_checkout_tools(registry, request)
    assert registry.annotations['ensure_checkout_map_with_reclaim'].destructiveHint is True
    listing = asyncio.run(registry.tools['list_checkout_maps']())
    assert listing['sessions'][0]['reclaim']['eligible']
    result = asyncio.run(registry.tools['ensure_checkout_map_with_reclaim'](**payload))
    assert result['reclaimed'] == {'sessionId': old, 'idleSeconds': 1200}
    assert result['sessionId'] != old
    assert client.get('/api/checkout-sessions/' + old).status_code == 410
    assert canonical.read_text() == '{"preserve":"canonical registry"}'


def test_mcp_catalog_pages_whole_identities_and_omits_large_build_diagnostics():
    """Long project/job errors do not hide exact temporary-folder metadata behind64k."""
    registry, calls, rows = ToolRegistry(), [], []
    for index in range(7):
        identifier = f'{index:032x}'
        rows.append({'sessionId': identifier, 'projectId': 'project-'+str(index), 'temporary': True,
            'path': '/private/Временные карты/'+identifier, 'checkout': '/checkout '+str(index),
            'ownerKey': 'task.'+str(index), 'label': 'Задача '+str(index), 'state': 'cleanup-failed',
            'ttlSeconds': 3600, 'createdAt': '2026-10-30T10:00:00+00:00',
            'expiresAt': '2026-10-30T11:00:00+00:00', 'activeLeases': 0,
            'project': {'id': 'project-'+str(index), 'snapshotId': 'snapshot-'+str(index),
                        'status': 'failed', 'error': 'large diagnostic ' * 20_000},
            'activeJob': {'error': 'other diagnostic ' * 20_000}, 'cleanupError': 'details require selected status'})

    async def request(method, path, body=None):
        """Return realistic long diagnostics while capturing the fixed private catalog route."""
        calls.append((method, path, body))
        return {'sessions': rows}

    register_checkout_tools(registry, request)
    first = asyncio.run(registry.tools['list_checkout_maps']())
    assert len(first['sessions']) == 4 and first['pagination']['nextOffset'] == 4
    second = asyncio.run(registry.tools['list_checkout_maps'](offset=4, limit=4))
    assert len(second['sessions']) == 3 and second['pagination']['nextOffset'] is None
    combined = first['sessions']+second['sessions']
    assert [item['sessionId'] for item in combined] == [row['sessionId'] for row in rows]
    for actual, expected in zip(combined, rows):
        for key in ('sessionId', 'projectId', 'path', 'checkout', 'expiresAt', 'state'):
            assert actual[key] == expected[key]
        assert 'error' not in actual['project'] and 'activeJob' not in actual and 'cleanupError' not in actual
    assert len(json.dumps(first, ensure_ascii=False).encode('utf-8')) < 64_000
    assert len(json.dumps(second, ensure_ascii=False).encode('utf-8')) < 64_000
    assert calls == [('GET', '/api/checkout-sessions', None)] * 2


@pytest.mark.parametrize('binding', [{'project_id': 'canonical'}, {'data_map_id': 'data-map'}])
def test_bound_mcp_connection_cannot_escape_into_any_temporary_tool(binding):
    """Every new proxy refuses canonical/data-map bindings before invoking transport."""
    registry, calls = ToolRegistry(), []

    async def request(method, path, body=None):
        """Any captured call would prove a scope escape and fail this test."""
        calls.append((method, path, body))
        return {}

    register_checkout_tools(registry, request, **binding)
    arguments = tool_arguments()
    assert set(arguments) == set(CHECKOUT_TOOLS) == set(registry.tools)
    for name in CHECKOUT_TOOLS:
        with pytest.raises(ValueError, match='unbound Atlas'):
            asyncio.run(registry.tools[name](**arguments[name]))
    assert calls == []
