"""Exercise early declarations with real checkout leases and a controlled builder."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier, Event

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from atlas import checkout_file_search
from atlas.checkout_maps import CheckoutMaps
from atlas.checkout_maps_api import register_checkout_routes
from atlas.checkout_maps_mcp import register_checkout_tools
from atlas.store import StoreError
from test_checkout_maps import Clocks, FakeStore
from test_checkout_maps_api import ToolRegistry


@pytest.fixture
def running_checkout(tmp_path):
    """Hold one private checkout in the running phase without launching Graphify."""
    root = tmp_path / 'source'
    root.mkdir()
    source = root / 'service.py'
    source.write_bytes(b'\xef\xbb\xbfclass Api:\r\n    def method(self, value: int):\r\n        return value\r\n')
    clocks = Clocks()
    manager = CheckoutMaps(tmp_path / 'atlas', clock=lambda: clocks.monotonic,
                           wall_clock=lambda: clocks.wall, store_factory=FakeStore)
    state = manager.ensure(root, 'early.read')
    sid = state['sessionId']
    entry = manager.entries[sid]
    entry.store.projects[entry.project_id]['status'] = 'running'
    app = FastAPI()
    register_checkout_routes(app, manager)

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        """Apply the same status mapping as the real host."""
        return JSONResponse({'error': str(error)}, status_code=error.status)

    try:
        with TestClient(app, base_url='http://127.0.0.1') as client:
            yield client, manager, clocks, source, sid
    finally:
        if entry.store.active_job:
            entry.store.complete()
        manager.close()


def test_eight_parallel_declaration_reads_while_map_builds(running_checkout):
    """Eight simultaneous calls see the same file and revision without opening a graph."""
    client, manager, clocks, source, sid = running_checkout
    expires = manager.status(sid)['expiresAt']
    clocks.advance(5)
    barrier = Barrier(8)

    def read(_):
        """Release all clients into the same private checkout at once."""
        barrier.wait(timeout=10)
        return client.post(f'/api/checkout-sessions/{sid}/file-search',
                           json={'path': 'service.py', 'query': 'API.METHOD'})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read, range(8)))
    assert all(row.status_code == 200 for row in results), [row.text for row in results]
    values = [row.json() for row in results]
    assert len({row['previewRevision'] for row in values}) == 1
    for value in values:
        assert value['checkoutSessionId'] == sid
        assert value['scope'] == 'file' and value['sourceKind'] == 'working-tree'
        assert value['build'] == {'jobId': 'building', 'status': 'running',
                                  'publishedSnapshotId': None, 'snapshotAvailable': False}
        assert 'snapshotId' not in value and 'edges' not in value
        assert value['sourceHash'] == sha256(source.read_bytes()).hexdigest()
        assert value['total'] == 1 and value['coverage']['status'] == 'complete'
        assert value['declarations'] == [{'name': 'method', 'kind': 'callable', 'owner': 'Api',
                                          'line': 2, 'endLine': 3, 'parameterList': '(self, value: int)',
                                          'textTruncated': False}]
    assert manager.status(sid)['activeLeases'] == 0
    assert manager.status(sid)['expiresAt'] > expires


def test_declared_csharp_type_and_overloads(running_checkout):
    """Preserve lexical owner and coordinates without collapsing overloads."""
    client, _, _, source, sid = running_checkout
    source.with_name('Service.cs').write_text('namespace Demo;\npublic class Api {\n'
                                               '    void Handle(int i) {}\n'
                                               '    void Handle(string value) {}\n}\n')
    endpoint = f'/api/checkout-sessions/{sid}/file-search'
    result = client.post(endpoint, json={'path': 'Service.cs', 'query': 'api.handle', 'limit': 1})
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['total'] == 2 and value['truncated']
    assert value['coverage']['status'] == 'complete'
    assert value['declarations'][0]['line'] == 3
    assert value['declarations'][0]['parameterList'] == '(int i)'


def test_partial_syntax_does_not_claim_complete_coverage(running_checkout):
    """Return independent declarations and report syntax recovery explicitly."""
    client, _, _, source, sid = running_checkout
    source.write_text('def useful():\n    return 1\n\ndef broken(:\n')
    result = client.post(f'/api/checkout-sessions/{sid}/file-search', json={'path': source.name})
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload['coverage']['status'] == 'partial'
    assert payload['coverage']['syntaxErrors'] is True
    assert [row['name'] for row in payload['declarations']] == ['useful']


def test_invalid_source_reads_do_not_extend_lifetime(running_checkout, tmp_path):
    """Reject aliases, external links, unsupported types and oversized source."""
    client, manager, clocks, source, sid = running_checkout
    outside = tmp_path / 'outside.py'
    outside.write_text('def confidential(): pass\n')
    source.with_name('external.py').symlink_to(outside)
    source.with_name('alias.py').symlink_to(source)
    source.with_name('large.py').write_bytes(b' ' * (checkout_file_search.MAX_SOURCE_BYTES + 1))
    source.with_name('binary.py').write_bytes(b'\xff')
    source.with_name('README.md').write_text('Example')
    endpoint = f'/api/checkout-sessions/{sid}/file-search'
    original_expiry = manager.status(sid)['expiresAt']
    clocks.advance(30)
    cases = [('../outside.py', 403), (str(outside), 403), ('external.py', 403),
             ('alias.py', 403), ('service.py/..', 403), ('missing.py', 404),
             ('large.py', 413), ('binary.py', 415), ('README.md', 415)]
    for path, status in cases:
        result = client.post(endpoint, json={'path': path})
        assert result.status_code == status, result.text
    assert client.post(endpoint, json={'path': source.name, 'limit': True}).status_code == 422
    assert manager.status(sid)['expiresAt'] == original_expiry


@pytest.mark.parametrize('mutation', ['source', 'rebuild', 'finish'])
def test_file_search_rechecks_source_and_session_before_response(running_checkout, monkeypatch, mutation):
    """Changes during parsing cannot produce an outdated successful read."""
    client, manager, clocks, source, sid = running_checkout
    entered, proceed = Event(), Event()
    original = checkout_file_search._search_declarations

    def delayed(*args):
        """Pause after reading the initial bytes and before the final fences."""
        entered.set()
        assert proceed.wait(timeout=10)
        return original(*args)

    monkeypatch.setattr(checkout_file_search, '_search_declarations', delayed)
    expires = manager.status(sid)['expiresAt']
    clocks.advance(5)
    endpoint = f'/api/checkout-sessions/{sid}/file-search'
    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(client.post, endpoint, json={'path': source.name})
        try:
            assert entered.wait(timeout=10)
            if mutation == 'source':
                source.write_text('def edited(): pass\n')
            elif mutation == 'rebuild':
                manager.entries[sid].store.complete()
                manager.rebuild(sid)
            else:
                assert manager.finish(sid)['state'] == 'deleting'
        finally:
            proceed.set()
        result = reading.result(timeout=10)
    assert result.status_code == (410 if mutation == 'finish' else 409), result.text
    assert manager.status(sid)['expiresAt'] == expires


def test_checkout_mcp_dispatches_to_file_search(running_checkout):
    """Use the actual proxy registration with the isolated HTTP service."""
    client, _, _, source, sid = running_checkout
    registry = ToolRegistry()

    async def request(method, path, body=None):
        """Forward one fixed MCP request into the real route."""
        assert (method, path) == ('POST', f'/api/checkout-sessions/{sid}/file-search')
        response = client.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    register_checkout_tools(registry, request)
    result = asyncio.run(registry.tools['search_checkout_file'](sid, source.name, 'method'))
    assert result['declarations'][0]['name'] == 'method'
    assert registry.annotations['search_checkout_file'].readOnlyHint is True


@pytest.mark.parametrize('build_status', ['queued', 'running'])
def test_building_graph_search_guides_mcp_to_file_or_status(running_checkout, build_status):
    """HTTP retains a typed 409, but MCP reports a normal status without search hits."""
    import httpx
    from atlas.mcp_transport import LoopbackTransport

    client, manager, clocks, _source, sid = running_checkout
    entry = manager.entries[sid]
    entry.store.projects[entry.project_id]['status'] = build_status
    expiry = manager.status(sid)['expiresAt']
    clocks.advance(30)
    endpoint = f'/api/checkout-sessions/{sid}/search'
    response = client.post(endpoint, json={'query': 'method'})
    assert response.status_code == 409 and response.headers['Retry-After'] == '1'
    expected = {'checkoutSessionId': sid, 'projectId': entry.project_id, 'state': 'building',
                'buildStatus': build_status, 'jobId': 'building', 'code': 'CHECKOUT_MAP_BUILDING',
                'retryAfterMs': 1000, 'temporary': True}
    assert response.json() == {**expected, 'error': response.json()['error']}
    assert manager.status(sid)['expiresAt'] == expiry

    def relay(request):
        """Send the loopback HTTP call through the real checkout ASGI route."""
        assert request.url.path == endpoint
        reply = client.request(request.method, request.url.path, content=request.content,
                               headers={'Content-Type': 'application/json'})
        return httpx.Response(reply.status_code, content=reply.content)

    async def discover():
        """Invoke the registered MCP tool through its production HTTP transport."""
        transport = LoopbackTransport(8765)
        registry = ToolRegistry()
        async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as transport.client:
            register_checkout_tools(registry, transport.request)
            return await registry.tools['search_checkout_nodes'](sid, query='method')

    payload = asyncio.run(discover())
    assert {key: payload[key] for key in expected} == expected
    assert 'nodes' not in payload and 'total' not in payload
    assert payload['nextCalls'] == [
        {'tool': 'get_checkout_map', 'arguments': {'sessionId': sid}},
        {'tool': 'search_checkout_file', 'arguments': {'sessionId': sid}, 'requires': ['path']},
    ]
    assert manager.status(sid)['expiresAt'] == expiry


def test_pinned_and_unrelated_conflicts_do_not_become_building_status(running_checkout):
    """Only the exact temporary search route may translate a typed conflict."""
    import json
    from atlas.mcp_transport import _checkout_build_pending

    client, manager, _clocks, _source, sid = running_checkout
    entry = manager.entries[sid]
    endpoint = f'/api/checkout-sessions/{sid}/search'
    response = client.post(endpoint, json={})
    payload = response.json()
    assert _checkout_build_pending('POST', endpoint, {'snapshotId': None}, 409, response.content)
    for method, path, body, status in [
        ('GET', endpoint, {'snapshotId': None}, 409),
        ('POST', endpoint + '/extra', {'snapshotId': None}, 409),
        ('POST', endpoint, {'snapshotId': 'stale'}, 409),
        ('POST', endpoint, {'snapshotId': None}, 404),
    ]:
        assert _checkout_build_pending(method, path, body, status, response.content) is None
    for change in [{'checkoutSessionId': 'b'*32}, {'code': 'other'}, {'jobId': None}, {'retryAfterMs': True}]:
        content = json.dumps({**payload, **change}).encode()
        assert _checkout_build_pending('POST', endpoint, {'snapshotId': None}, 409, content) is None

    def stale(*args):
        """Simulate the ordinary graph store rejecting an explicitly pinned revision."""
        raise StoreError('Pinned snapshot is stale.', 409)

    entry.store.search_project_nodes = stale
    pinned = client.post(endpoint, json={'snapshotId': 'stale'})
    assert pinned.status_code == 409
    assert pinned.json() == {'error': 'Pinned snapshot is stale.'}
