"""Public position contract and host snapshot/lease boundaries, using the real engine."""

import asyncio
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
import pytest

from atlas.checkout_maps import CheckoutMaps
from atlas.checkout_maps_api import register_checkout_routes
from atlas.position_context_api import register_position_context_routes
from atlas.position_context_mcp import POSITION_CONTEXT_TOOLS, register_position_context_tools
from atlas.store import StoreError
from test_checkout_maps import Clocks, FakeStore


PROGRAM = ('def top():\n    return middle(None)\n\n'
           'def middle(value):\n    return bottom(value)\n\n'
           'def bottom(value):\n    return leaf(value)\n\n'
           'def leaf(value):\n    return value\n\n'
           'def sibling():\n    return middle(1)\n')


class Published:
    """Allow controlled publication races while retaining exact source and graph objects."""

    def __init__(self, root, graph, project='permanent'):
        """Choose the trusted host root and currently published revision."""
        self.root, self.graph, self.project, self.snapshot = root, graph, project, 'one'
        self.reads = 0

    def assert_snapshot(self, project, snapshot):
        """Reject foreign project and stale revisions before any source access."""
        if project != self.project:
            raise StoreError('Unknown project', 404)
        if snapshot != self.snapshot:
            raise StoreError('Snapshot changed', 409)

    def comparison_snapshot(self, project):
        """Return shared immutable input so accidental mutations are observable."""
        self.reads += 1
        return self.root, self.graph


class PrivatePublished(FakeStore):
    """Real CheckoutMaps ownership with a controlled snapshot, without executing Graphify."""

    def __init__(self, data, graph):
        """Initialize the existing lifecycle fake and retain one explicit captured graph."""
        super().__init__(data)
        self.graph, self.snapshot = graph, 'one'

    def assert_snapshot(self, project, snapshot):
        """Only the selected private registry and pinned revision are accessible."""
        if project not in self.projects:
            raise StoreError('Foreign project', 404)
        if snapshot != self.snapshot:
            raise StoreError('Snapshot changed', 409)

    def comparison_snapshot(self, project):
        """Derive source selection solely from the private store's trusted registry."""
        return Path(self.projects[project]['path']), self.graph


@pytest.fixture
def position_api(tmp_path):
    """Share a small real Python source graph between two independently scoped host stores."""
    root = tmp_path / 'checkout'
    root.mkdir()
    (root / 'sample.py').write_text(PROGRAM)
    digest = sha256(PROGRAM.encode()).hexdigest()
    graph = {'metadata': {'snapshotId': 'one', 'analysisStatus': 'partial'}, 'nodes': [],
             'edges': [{'source': a, 'target': b, 'relation': 'calls', 'confidence': 1}
                       for a, b in [('top', 'middle'), ('middle', 'bottom'),
                                    ('bottom', 'leaf'), ('sibling', 'middle')]]}
    for name, line in [('top', 1), ('middle', 4), ('bottom', 7), ('leaf', 10), ('sibling', 13)]:
        graph['nodes'].append({'id': name, 'label': name+'()', 'file': 'sample.py',
            'kind': 'function', 'line': line,
            'commentSource': {'sourceKind': 'snapshot-captured-source', 'sourceHash': digest}})
    permanent = Published(root, graph)
    clocks = Clocks()
    manager = CheckoutMaps(tmp_path / 'data', clock=lambda: clocks.monotonic,
        wall_clock=lambda: clocks.wall, store_factory=lambda data: PrivatePublished(data, graph))
    app = FastAPI()
    register_checkout_routes(app, manager)
    register_position_context_routes(app, permanent, manager)

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        """Keep the established service error semantics in this focused host fixture."""
        return JSONResponse({'error': str(error)}, status_code=error.status)

    try:
        with TestClient(app) as client:
            session = manager.ensure(root, 'position-tests')
            manager.entries[session['sessionId']].store.complete()
            yield client, permanent, manager, clocks, session['sessionId']
    finally:
        manager.close()


def test_real_engine_http_default_depth_and_scope_parity(position_api):
    """Same host facts produce the same dependencies, without changing source or graph."""
    client, published, _, _, sid = position_api
    before_graph, before_source = deepcopy(published.graph), (published.root / 'sample.py').read_bytes()
    body = {'snapshotId': 'one', 'file': 'sample.py', 'line': 5}
    canonical = client.post('/api/projects/permanent/position-dependencies', json=body)
    private = client.post('/api/checkout-sessions/'+sid+'/position-dependencies', json=body)
    assert canonical.status_code == private.status_code == 200, (canonical.text, private.text)
    first, second = canonical.json(), private.json()
    assert first['anchorId'] == second['anchorId'] == 'middle'
    assert {node['id'] for node in first['nodes']} == {'top', 'sibling', 'middle', 'bottom', 'leaf'}
    for key in ['version', 'snapshotId', 'incoming', 'outgoing', 'edges', 'status']:
        assert first[key] == second[key]
    evidence = [deepcopy(result['sourceEvidence']) for result in (first, second)]
    for facts in evidence:
        for caller in facts.get('callers', []):
            provenance = caller['sourceProvenance']
            assert provenance['status'] == 'match'
            assert provenance['currentSourceHash'] == sha256(before_source).hexdigest()
            assert datetime.fromisoformat(provenance['currentReadAt']).tzinfo is not None
            # These are two actual reads; only their observation time may differ.
            provenance['currentReadAt'] = None
    assert evidence[0] == evidence[1]
    assert second['checkoutSessionId'] == sid and second['temporary'] is True
    assert published.graph == before_graph and (published.root / 'sample.py').read_bytes() == before_source
    assert len(json.dumps(second, ensure_ascii=False).encode()) <= 64_000


@pytest.mark.parametrize('extra', [{'line': 0}, {'line': True}, {'line': '5'}, {'depth': 0},
    {'depth': 9}, {'maxNodes': 121}, {'maxEdges': 241}, {'maxEdges': True},
    {'direction': 'up'}, {'expectedSourceHash': 'short'}, {'graph': {}}, {'sourceFacts': []}])
def test_http_strict_contract_never_reads_or_renews_on_invalid_input(position_api, extra):
    client, published, manager, clocks, sid = position_api
    before = manager.status(sid)['expiresAt']
    clocks.advance(10)
    response = client.post('/api/checkout-sessions/'+sid+'/position-dependencies', json={
        'snapshotId': 'one', 'file': 'sample.py', 'line': 5, **extra})
    assert response.status_code == 422, response.text
    assert published.reads == 0 and manager.status(sid)['expiresAt'] == before


def test_snapshot_checks_and_oversized_response_precede_useful_touch(position_api, monkeypatch):
    """Publication races and final scope-envelope failures discard the complete response."""
    client, published, manager, clocks, sid = position_api
    path = '/api/checkout-sessions/'+sid+'/position-dependencies'
    body = {'snapshotId': 'one', 'file': 'sample.py', 'line': 5}
    before = manager.status(sid)['expiresAt']
    clocks.advance(30)
    assert client.post(path, json={**body, 'snapshotId': 'old'}).status_code == 409
    assert manager.status(sid)['expiresAt'] == before
    import atlas.position_context_api as api
    original = api.build_position_context

    def too_large(*args):
        """Force envelope rejection after actual analysis instead of mocking an HTTP status."""
        return {**original(*args), 'padding': 'x' * 64_000}

    monkeypatch.setattr(api, 'build_position_context', too_large)
    assert client.post(path, json=body).status_code == 413
    assert manager.status(sid)['expiresAt'] == before

    def publication_race(*args):
        """Publish after source analysis to challenge the final snapshot fence."""
        result = original(*args)
        published.snapshot = 'two'
        return result

    monkeypatch.setattr(api, 'build_position_context', publication_race)
    assert client.post('/api/projects/permanent/position-dependencies', json=body).status_code == 409


def test_successful_ttl_and_deleted_scope_use_existing_lifecycle(position_api):
    client, _, manager, clocks, sid = position_api
    before = manager.status(sid)['expiresAt']
    clocks.advance(30)
    path = '/api/checkout-sessions/'+sid+'/position-dependencies'
    body = {'snapshotId': 'one', 'file': 'sample.py', 'line': 5}
    assert client.post(path, json=body).status_code == 200
    assert manager.status(sid)['expiresAt'] > before
    assert manager.finish(sid)['state'] == 'deleted'
    assert client.post(path, json=body).status_code == 410


def test_private_publication_and_editor_hash_failures_never_renew(position_api, monkeypatch):
    """A completed computation does not authorize touch after private publication changed."""
    client, _, manager, clocks, sid = position_api
    path = '/api/checkout-sessions/'+sid+'/position-dependencies'
    body = {'snapshotId': 'one', 'file': 'sample.py', 'line': 5}
    before = manager.status(sid)['expiresAt']
    clocks.advance(30)
    assert client.post(path, json={**body, 'expectedSourceHash': '0'*64}).status_code == 409
    assert manager.status(sid)['expiresAt'] == before
    import atlas.position_context_api as api
    original = api.build_position_context

    def publish_after_computation(*args):
        """Move the selected private publisher while preserving its lifecycle owner."""
        result = original(*args)
        manager.entries[sid].store.snapshot = 'two'
        return result

    monkeypatch.setattr(api, 'build_position_context', publish_after_computation)
    assert client.post(path, json=body).status_code == 409
    assert manager.status(sid)['expiresAt'] == before


@pytest.mark.parametrize('padding', ['Ж'*33000, '😀"\\'*10000, 'x'*63960])
def test_utf8_escaping_and_scope_envelope_count_towards_final_budget(position_api, monkeypatch, padding):
    """Count encoded Unicode/escaping and private identity fields, rather than character length."""
    client, _, manager, clocks, sid = position_api
    before = manager.status(sid)['expiresAt']
    clocks.advance(30)
    import atlas.position_context_api as api

    def bounded_core(*args):
        """Model a potentially near-limit core response before the host adds its own identity."""
        return {'snapshotId': 'one', 'padding': padding}

    monkeypatch.setattr(api, 'build_position_context', bounded_core)
    response = client.post('/api/checkout-sessions/'+sid+'/position-dependencies', json={
        'snapshotId': 'one', 'file': 'sample.py', 'line': 5})
    assert response.status_code == 413
    assert manager.status(sid)['expiresAt'] == before


def test_mcp_actual_argument_validation_http_parity_and_connection_binding(position_api):
    """Exercise FastMCP's real tool parser and both HTTP routes, with no hidden graph store."""
    client, _, _, _, sid = position_api
    server, calls = FastMCP('position-fixture'), []

    async def request(method, path, body):
        """Use the actual HTTP contract; count transport to prove early binding refusal."""
        calls.append((method, path, body))
        response = client.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def selected(project):
        """Model the existing selected-project guard before network access."""
        if project not in {None, 'permanent'}:
            raise ValueError('Different project')
        return 'permanent'

    register_position_context_tools(server, request, selected)

    async def scenario():
        tools = await server.list_tools()
        assert {t.name for t in tools} == set(POSITION_CONTEXT_TOOLS)
        for tool in tools:
            assert tool.inputSchema['properties']['depth']['default'] == 2
        arguments = {'snapshotId': 'one', 'file': 'sample.py', 'line': 5}
        permanent = json.loads((await server.call_tool('get_position_dependencies', arguments))[0].text)
        temporary = json.loads((await server.call_tool('get_checkout_position_dependencies', {**arguments, 'sessionId': sid}))[0].text)
        assert permanent['anchorId'] == temporary['anchorId'] == 'middle'
        assert calls[0][2] == calls[1][2]
        count = len(calls)
        with pytest.raises(Exception):
            await server.call_tool('get_position_dependencies', {**arguments, 'depth': True})
        with pytest.raises(Exception):
            await server.call_tool('get_position_dependencies', {**arguments, 'projectId': 'other'})
        assert len(calls) == count
        for binding in [{'project_id': 'permanent'}, {'data_map_id': 'data-only'}]:
            bound = FastMCP('bound-position')
            register_position_context_tools(bound, request, selected, **binding)
            with pytest.raises(Exception):
                await bound.call_tool('get_checkout_position_dependencies', {**arguments, 'sessionId': sid})
        assert len(calls) == count

    asyncio.run(scenario())
