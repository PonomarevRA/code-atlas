"""Lifecycle, isolation and evidence tests for temporary captured browser graphs."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest
from pydantic import ValidationError

from atlas.browser_models import BrowserCreate, BrowserQuery, BrowserNode, BrowserSource
from atlas.browser_sessions import BrowserSessions, BrowserSessionError
from atlas import browser_sessions
from atlas.server import create_app
from atlas.agent_mcp import create_mcp
from atlas.browser_mcp import BROWSER_TOOLS


def capture():
    """A rendered button and real JS declarations carry a private canary through all reads."""
    return {'schemaVersion': 'atlas-browser-capture-v1',
            'page': {'url': 'https://example.test/app?token=PRIVATE_QUERY', 'title': 'Fixture', 'capturedAt': '2026-10-26T10:00:00Z'},
            'nodes': [{'id': '1', 'tag': 'main'}, {'id': '2', 'parentId': '1', 'tag': 'button',
                       'attributes': {'id': 'save'}, 'text': 'PRIVATE_BROWSER_CANARY', 'bounds': [1., 2., 3., 4.]}],
            'scripts': [{'id': 's1', 'url': 'https://example.test/app.js', 'status': 'captured',
                         'source': 'function send(){fetch("/api/save")} function save(){document.querySelector("#save");send()}'}]}


def session_store():
    """Expose an adjustable monotonic clock without sleeps or filesystem state."""
    now = [0.]
    return BrowserSessions(clock=lambda: now[0], wall_clock=lambda: 1000.), now


@pytest.mark.parametrize('mode', ['task', 'hour'])
def test_ttl_boundary_and_wall_clock_independence(mode):
    """An exact elapsed deadline invalidates every read even without running the sweeper."""
    store, now = session_store()
    item = store.create(BrowserCreate(capture=capture(), retention=mode, ttlSeconds=10))
    identifier = item['sessionId']
    now[0] = 9.99
    assert store.query(identifier, BrowserQuery(query='save'))['total'] >= 1
    now[0] = 10.
    for read in (lambda: store.query(identifier, BrowserQuery()),
                 lambda: store.node(identifier, BrowserNode(nodeId='dom:2')),
                 lambda: store.source(identifier, BrowserSource(scriptId='s1'))):
        with pytest.raises(BrowserSessionError, match='expired'):
            read()
    assert store.list()['sessions'] == []


@pytest.mark.parametrize('reason', ['completed', 'cancelled', 'error'])
def test_finish_releases_sources_and_is_idempotent(reason):
    """Success, failure and cancellation share deletion, without persistent source copies."""
    store, _ = session_store()
    identifier = store.create(BrowserCreate(capture=capture()))['sessionId']
    store.finish(identifier, reason)
    assert store.finish(identifier, reason)['deleted']
    assert store.entries == {}
    with pytest.raises(BrowserSessionError):
        store.source(identifier, BrowserSource(scriptId='s1'))
    assert BrowserSessions().list()['sessions'] == []


def test_graph_candidates_source_hash_and_unicode_pagination():
    """Real parser output stays lexical; source pages reconstruct original Unicode bytes."""
    store, _ = session_store()
    data = capture()
    data['scripts'][0]['source'] += '\n// 😀 PRIVATE_BROWSER_CANARY'
    identifier = store.create(BrowserCreate(capture=data))['sessionId']
    found = store.query(identifier, BrowserQuery(kind='function', query='save'))
    assert found['total'] == 2
    found['nodes'] = [node for node in found['nodes'] if node['label'] == 'save']
    detail = store.node(identifier, BrowserNode(nodeId=found['nodes'][0]['id']))
    assert any(edge['relation'] == 'call_candidate' for edge in detail['edges'])
    assert all(edge.get('runtimeSelectionKnown') is not True for edge in detail['edges'])
    assert detail['node']['lexicalFacts'][0]['kind'] == 'dom-selector-expression'
    pieces, offset = [], 0
    while True:
        page = store.source(identifier, BrowserSource(scriptId='s1', offset=offset, limit=7))
        pieces.append(page['text'])
        if page['nextOffset'] is None:
            break
        offset = page['nextOffset']
    assert ''.join(pieces) == data['scripts'][0]['source']
    assert len(page['sourceHash']) == 64
    assert store.list()['sessions'][0]['page']['url'] == 'https://example.test/app'


def test_capacity_failed_creation_and_shutdown(monkeypatch):
    """Rejected admission cannot evict live captures or publish a map after shutdown."""
    store, _ = session_store()
    first = store.create(BrowserCreate(capture=capture()))
    monkeypatch.setattr(browser_sessions, 'MAX_RETAINED_BYTES', 1)
    with pytest.raises(BrowserSessionError) as rejected:
        store.create(BrowserCreate(capture=capture()))
    assert rejected.value.status == 429
    assert len(store.list()['sessions']) == 1
    store.close()
    assert store.entries == {}
    with pytest.raises(BrowserSessionError):
        store.query(first['sessionId'], BrowserQuery())
    with pytest.raises(BrowserSessionError):
        store.create(BrowserCreate(capture=capture()))


@pytest.mark.parametrize('change', ['cycle', 'duplicate', 'source-status', 'form-value', 'ttl', 'oversized-source'])
def test_invalid_captures_fail_before_session_publication(change):
    """Malformed trees, status lies and excessive source are rejected by the public DTO."""
    data = {'capture': capture()}
    if change == 'cycle': data['capture']['nodes'][0]['parentId'] = '2'
    if change == 'duplicate': data['capture']['nodes'][1]['id'] = '1'
    if change == 'source-status': data['capture']['scripts'][0]['status'] = 'unavailable'
    if change == 'form-value': data['capture']['nodes'][0]['attributes'] = {'value': 'private'}
    if change == 'ttl': data['ttlSeconds'] = 3601
    if change == 'oversized-source': data['capture']['scripts'][0]['source'] = '😀' * (300 * 1024)
    with pytest.raises(ValidationError):
        BrowserCreate.model_validate(data)


def test_http_mcp_registration_privacy_and_origin_boundary(tmp_path, monkeypatch):
    """Real HTTP adapters preserve cross-origin refusal and never journal private query/labels."""
    from atlas import browser_privacy

    @contextmanager
    def synthetic_capture(value):
        """Expose only the synthetic fixture while testing session routing and isolation."""
        yield value, {'mode': 'off'}

    gate = SimpleNamespace(capture=synthetic_capture, close=lambda: None)
    monkeypatch.setattr(browser_privacy, 'BrowserPrivacy', lambda _: gate)
    app = create_app(tmp_path)
    recorded = []
    monkeypatch.setattr(app.state.agent_activity, 'submit', recorded.append)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        assert app.state.browser_sessions.thread.is_alive()
        response = client.post('/api/browser-sessions', json={'capture': capture()},
                               headers={'x-atlas-mcp-session': 'a' * 32})
        assert response.status_code == 200, response.text
        identifier = response.json()['sessionId']
        headers = {'x-atlas-mcp-session': 'a' * 32}
        path = '/api/browser-sessions/' + identifier
        result = client.post(path + '/query', json={'query': 'PRIVATE_BROWSER_CANARY'}, headers=headers)
        assert result.json()['total'] == 1
        assert client.post(path + '/source', json={'scriptId': 's1'}, headers=headers).status_code == 200
        assert client.post('/api/browser-sessions', json={'capture': capture()}, headers={'Origin': 'https://evil.test'}).status_code == 403
        assert client.get('/api/projects').json()['projects'] == []
        assert recorded == []
        assert client.post(path + '/finish', json={'reason': 'error'}, headers=headers).json()['deleted']
        assert client.post(path + '/node', json={'nodeId': 'dom:2'}).status_code == 404
    assert app.state.browser_sessions.entries == {}
    assert not app.state.browser_sessions.thread.is_alive()
    for path in tmp_path.rglob('*'):
        if path.is_file():
            assert b'PRIVATE_BROWSER_CANARY' not in path.read_bytes()


def test_scoped_mcp_rejects_browser_calls_before_http(monkeypatch):
    """A repository-pinned MCP session has no implied authority for a user's browser tab."""
    requests = []
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json={}))))

    async def scenario():
        """Use registered SDK tools so catalog and scope checks are exercised together."""
        server = create_mcp(project_id='one')
        assert set(BROWSER_TOOLS) <= {tool.name for tool in await server.list_tools()}
        for name, args in [('list_browser_sessions', {}), ('create_browser_session', {'capture': capture()}),
                           ('query_browser_session', {'sessionId': 'a' * 32}),
                           ('get_browser_node', {'sessionId': 'a' * 32, 'nodeId': 'n'}),
                           ('read_browser_script', {'sessionId': 'a' * 32, 'scriptId': 'x'}),
                           ('finish_browser_session', {'sessionId': 'a' * 32})]:
            with pytest.raises(Exception, match='unscoped'):
                await server.call_tool(name, args)
        assert requests == []
    asyncio.run(scenario())


def test_deep_call_chain_has_bounded_cpu_cost():
    """A tiny nested chain must not trigger repeated root-to-parent traversal per call."""
    import time
    data = capture()
    data['scripts'][0]['source'] = 'function f(){return f' + '()' * 1200 + '}'
    store, _ = session_store()
    began = time.monotonic()
    identifier = store.create(BrowserCreate(capture=data))['sessionId']
    elapsed = time.monotonic() - began
    assert elapsed < 2, f'Nested call-chain capture took {elapsed:.3f}s'
    assert store.query(identifier, BrowserQuery(kind='function'))['total'] == 1


def test_input_warnings_cannot_hide_parser_failure():
    """Each script retains its analysis status even when aggregate diagnostics overflow."""
    data = capture()
    data['warnings'] = ['input-warning-' + str(i) for i in range(32)]
    data['scripts'][0]['source'] = 'function {'
    store, _ = session_store()
    identifier = store.create(BrowserCreate(capture=data))['sessionId']
    detail = store.node(identifier, BrowserNode(nodeId='script:s1'))
    assert detail['node']['analysisStatus'] == 'parse-error'
    result = store.query(identifier, BrowserQuery())
    assert 'javascript-parse-error:s1' in result['warnings']
    assert result['warningsTruncated'] is True
    assert len(store.entries[identifier]['graph']['warnings']) == 32
