"""Agent-facing bounds, original-source navigation and recovery on real registered tools."""
import asyncio
import json
import pytest
from atlas.browser_models import BrowserCreate, BrowserNode, BrowserNodeSource, BrowserProperty, BrowserQuery
from atlas.browser_sessions import BrowserSessionError
from atlas.browser_mcp import register_browser_tools, BROWSER_TOOLS
from mcp.server.fastmcp import FastMCP
from test_browser_sessions import capture, session_store


def test_node_relative_unicode_source_nested_function_and_expiry():
    """A Unicode prefix never changes the exact function slice or its full-script identity."""
    store, now = session_store()
    data = capture()
    data['scripts'][0]['source'] = '// 😀é\nfunction outer(){function inner(){return "界"}return inner()}'
    identifier = store.create(BrowserCreate(capture=data, ttlSeconds=10))['sessionId']
    nodes = store.query(identifier, BrowserQuery(kind='function', query='inner'))['nodes']
    node = nodes[0]
    pages, offset = [], 0
    while True:
        page = store.node_source(identifier, BrowserNodeSource(nodeId=node['id'], offset=offset, limit=7))
        pages.append(page['text'])
        if page['nextOffset'] is None:
            break
        offset = page['nextOffset']
    assert ''.join(pages) == 'function inner(){return "界"}'
    assert page['coordinateKind'] == 'node-relative-unicode-codepoints'
    assert len(page['sourceHash']) == 64
    now[0] = 10
    with pytest.raises(BrowserSessionError):
        store.node_source(identifier, BrowserNodeSource(nodeId=node['id']))
    assert store.entries == {}


def test_large_valid_dom_is_readable_and_properties_are_lossless():
    """A 64KB-plus accepted DOM node has a compact projection and exact indexed detail reads."""
    store, _ = session_store()
    data = capture()
    attrs = {('界' * 100 + str(i)): '😀' * 1024 for i in range(16)}
    data['nodes'][0]['attributes'] = attrs
    identifier = store.create(BrowserCreate(capture=data))['sessionId']
    result = store.node(identifier, BrowserNode(nodeId='dom:1', limit=50))
    assert len(json.dumps(result).encode()) < 64000
    assert result['node']['omittedFields'] == [{'field': 'attributes', 'propertyCount': 16}]
    first = store.property(identifier, BrowserProperty(nodeId='dom:1', field='attributes', limit=512))
    second = store.property(identifier, BrowserProperty(nodeId='dom:1', field='attributes', offset=first['nextOffset']))
    assert first['key'] == next(iter(attrs))
    assert first['text'] + second['text'] == next(iter(attrs.values()))
    assert second['nextOffset'] is None


def test_lexical_search_pages_preserve_order_total_and_scope():
    """Selector/request expressions are searchable; pagination returns only its window."""
    store, _ = session_store()
    identifier = store.create(BrowserCreate(capture=capture()))['sessionId']
    assert store.query(identifier, BrowserQuery(query='/api/save'))['nodes'][0]['label'] == 'send'
    assert store.query(identifier, BrowserQuery(query='#save', kind='function'))['nodes'][0]['label'] == 'save'
    all_nodes = store.query(identifier, BrowserQuery())
    first = store.query(identifier, BrowserQuery(limit=2))
    second = store.query(identifier, BrowserQuery(offset=first['nextOffset'], limit=50))
    assert first['total'] == second['total'] == all_nodes['total']
    assert first['nodes'] + second['nodes'] == all_nodes['nodes']
    assert second['nextOffset'] is None
    assert store.overview(identifier)['counts']['function'] == 2
    store.finish(identifier, 'completed')
    assert store.entries == {}


def test_registered_sdk_bounds_annotations_and_guard_precede_http():
    """Use the actual FastMCP schema/argument parser; malformed requests cannot reach HTTP."""
    async def scenario():
        requests = []
        async def request(*args):
            """Track only valid proxy calls, without retaining captured content elsewhere."""
            requests.append(args)
            return {}
        server = FastMCP('test')
        register_browser_tools(server, request)
        catalog = {tool.name: tool for tool in await server.list_tools()}
        assert set(BROWSER_TOOLS) <= catalog.keys()
        assert all(catalog[name].outputSchema is not None for name in BROWSER_TOOLS)
        schema = catalog['query_browser_session'].inputSchema['properties']
        assert schema['limit']['maximum'] == 50
        assert schema['offset']['minimum'] == 0
        assert schema['sessionId']['pattern']
        assert '$ref' in catalog['create_browser_session'].inputSchema['properties']['capture']
        assert catalog['finish_browser_session'].annotations.idempotentHint is True
        assert all(catalog[name].annotations.openWorldHint is False for name in BROWSER_TOOLS)
        for name, args in [('query_browser_session', {'sessionId': 'a'*32, 'limit': 51}),
                           ('read_browser_node_source', {'sessionId': 'a'*32, 'nodeId':'x', 'offset':-1})]:
            with pytest.raises(Exception):
                await server.call_tool(name, args)
        assert requests == []
        for scope in ({'project_id':'p'}, {'data_map_id':'d'}):
            scoped = FastMCP('scoped')
            register_browser_tools(scoped, request, **scope)
            for name,args in [('get_browser_capabilities',{}), ('get_browser_session',{'sessionId':'a'*32}),
                              ('read_browser_node_source',{'sessionId':'a'*32,'nodeId':'x'}),
                              ('read_browser_node_property',{'sessionId':'a'*32,'nodeId':'x','field':'text'})]:
                with pytest.raises(Exception, match='unscoped'):
                    await scoped.call_tool(name,args)
        assert requests == []
    asyncio.run(scenario())


@pytest.mark.parametrize('status,code,recovery', [(503,'browser-busy','retry-later'), (404,'browser-item-unavailable','select-live-session-or-node'), (429,'browser-capacity','finish-owned-session')])
def test_registered_mcp_preserves_safe_structured_recovery(monkeypatch,status,code,recovery):
    """The complete registered tool/HTTP path keeps machine fields while dropping hostile remote text."""
    import httpx
    from atlas.agent_mcp import create_mcp
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(**kw,transport=httpx.MockTransport(
        lambda request: httpx.Response(status,json={'error':'PRIVATE_REMOTE_TEXT','code':'untrusted-code','recoveryAction':'execute-site-code'}))))
    async def scenario():
        server=create_mcp()
        result=await server.call_tool('get_browser_session',{'sessionId':'a'*32})
        structured=result[1]
        assert structured['code']==code
        assert structured['recoveryAction']==recovery
        assert 'PRIVATE' not in json.dumps(structured)
        assert structured['retryable'] is (status in {429,503})
    asyncio.run(scenario())


@pytest.fixture(autouse=True)
def synthetic_browser_product_profile(monkeypatch):
    """Legacy raw fixtures explicitly opt out; privacy boundary has its own strict native tests."""
    import atlas.browser_privacy as privacy_module
    real = privacy_module.BrowserPrivacy
    def make(directory):
        gate = real(directory)
        if gate.status()['available']:
            gate.select_after_human_confirmation('off')
        return gate
    monkeypatch.setattr(privacy_module, 'BrowserPrivacy', make)
