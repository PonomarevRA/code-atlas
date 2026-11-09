"""HTTP/MCP читают один immutable run с ограниченными страницами и source fence."""
import asyncio
import json
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from atlas.architecture_api import register_architecture_routes
from atlas.architecture_service import ArchitectureChecks
from atlas.store import StoreError
from test_architecture_engine import config, fixture


class Store:
    """Контракт store с управляемой публикацией новой карты для race-тестов."""
    def __init__(self, root):
        self.root, self.snapshot = root, 's'
    def comparison_snapshot(self, project):
        if project != 'p':
            raise StoreError('not found', 404)
        return self.root, {'metadata': {'snapshotId': self.snapshot}, 'nodes': [], 'edges': []}
    def assert_snapshot(self, project, snapshot):
        self.comparison_snapshot(project)
        if snapshot != self.snapshot:
            raise StoreError('stale', 409)


@pytest.fixture
def client(tmp_path):
    fixture(tmp_path, cycle=True)
    state = Store(tmp_path)
    app = FastAPI()
    register_architecture_routes(app, state)
    @app.exception_handler(StoreError)
    def handle(_, error):
        return JSONResponse({'error': str(error)}, status_code=error.status)
    with TestClient(app) as client:
        yield client, state, app.state.architecture_checks


def create(client, rules=None):
    response = client.post('/api/projects/p/architecture-checks', json={'snapshotId': 's', 'rules': (rules or config()).model_dump()})
    assert response.status_code == 200, response.text
    return response.json()


def test_page_evidence_source_and_snapshot_context(client):
    http, state, _ = client
    run = create(http)
    base = '/api/projects/p/architecture-checks/' + run['runId']
    assert run['status'] == 'violated' and run['items'][0]['status'] == 'violated'
    findings = http.get(base, params={'collection': 'findings', 'limit': 1}).json()
    proof = http.get(base + '/findings/' + findings['items'][0]['id']).json()
    assert proof['items'][0]['source'] == 'a/main.py'
    source = http.post(base + '/findings/' + proof['findingId'] + '/source', json={'edgeIndex': 0})
    assert source.status_code == 200
    assert 'from b.service import work' in source.json()['text']
    assert source.json()['snapshotId'] == 's'
    assert source.json()['sourceByteHash'] == proof['items'][0]['sourceHash']
    (state.root / 'a/main.py').write_text('changed=True\n')
    assert http.post(base + '/findings/' + proof['findingId'] + '/source', json={'edgeIndex': 0}).status_code == 409
    assert http.get(base.replace('/p/', '/other/')).status_code == 404
    state.snapshot = 's2'
    assert http.get(base).status_code == 409


def test_cycle_pages_are_retained_not_recomputed(client):
    http, state, _ = client
    run = create(http, config('cycles'))
    base = '/api/projects/p/architecture-checks/' + run['runId']
    finding = http.get(base, params={'collection': 'findings'}).json()['items'][0]
    route = base + '/findings/' + finding['id']
    first = http.get(route, params={'limit': 1}).json()
    assert first['total'] == 2 and first['nextOffset'] == 1
    (state.root / 'b/service.py').write_text('pass\n')
    second = http.get(route, params={'offset': first['nextOffset'], 'limit': 1}).json()
    assert second['sourceFingerprint'] == first['sourceFingerprint']
    assert second['items'][0]['target'] == first['items'][0]['source']
    assert second['nextOffset'] is None


def test_schema_invalid_ids_and_offsets(client):
    http, _, _ = client
    assert http.post('/api/projects/p/architecture-checks', json={'rules': config().model_dump()}).status_code == 422
    value = config().model_dump(); value['execute'] = 'no'
    assert http.post('/api/projects/p/architecture-checks', json={'snapshotId': 's', 'rules': value}).status_code == 422
    run = create(http); base = '/api/projects/p/architecture-checks/' + run['runId']
    assert http.get(base, params={'limit': 21}).status_code == 422
    assert http.get(base, params={'offset': -1}).status_code == 422
    assert http.get(base + '/findings/missing').status_code == 404
    assert http.get(base, params={'offset': 100000}).json()['items'] == []


def test_snapshot_publication_during_capture_is_rejected(client, monkeypatch):
    import atlas.architecture_service as module
    http, state, _ = client
    original = module.capture
    def changed(*args):
        facts = original(*args)
        state.snapshot = 's2'
        return facts
    monkeypatch.setattr(module, 'capture', changed)
    response = http.post('/api/projects/p/architecture-checks', json={'snapshotId': 's', 'rules': config().model_dump()})
    assert response.status_code == 409


def test_ttl_eviction_busy_and_bounded_cursor(client, monkeypatch):
    import atlas.architecture_service as module
    http, state, service = client
    clock = [0]
    service.clock = lambda: clock[0]
    monkeypatch.setattr(module, 'MAX_RUNS', 1)
    first = create(http); second = create(http)
    route = '/api/projects/p/architecture-checks/'
    assert http.get(route + first['runId']).status_code == 404
    service.analysis_slot.acquire()
    assert http.post(route.rstrip('/'), json={'snapshotId': 's', 'rules': config().model_dump()}).status_code == 429
    service.analysis_slot.release()
    page = service._bounded_page({}, [{'text': 'x' * 32000}] * 3, 5, 8)
    assert len(json.dumps(page).encode()) <= 64000
    assert page['nextOffset'] == 5 + len(page['items']) and page['deliveryTruncated']
    clock[0] = module.TTL_SECONDS
    assert http.get(route + second['runId']).status_code == 404


def test_mcp_is_thin_and_bound_project_guard_precedes_http():
    from atlas.architecture_mcp import register_architecture_tools
    class MCP:
        def __init__(self): self.tools = {}
        def tool(self, **_):
            def add(fn): self.tools[fn.__name__] = fn; return fn
            return add
    calls = []
    async def request(method, path, body=None):
        calls.append((method, path, body)); return {'status': 'unknown'}
    def selected(project):
        if project not in (None, 'p'): raise ValueError('bound')
        return 'p'
    mcp = MCP(); register_architecture_tools(mcp, request, selected)
    run = mcp.tools['run_architecture_checks']
    assert asyncio.run(run('s', config())) == {'status': 'unknown'}
    assert calls[-1][2]['rules'] == config().model_dump()
    with pytest.raises(ValueError): asyncio.run(run('s', config(), 'other'))
    assert len(calls) == 1
    asyncio.run(mcp.tools['read_architecture_evidence']('run', 'finding', offset=1, limit=2))
    assert calls[-1][1] == '/api/projects/p/architecture-checks/run/findings/finding?offset=1&limit=2'


def test_real_mcp_schema_registers_tools():
    from atlas.agent_mcp import create_mcp
    server = create_mcp(project_id='p')
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert {'run_architecture_checks', 'read_architecture_checks', 'read_architecture_evidence', 'read_architecture_source'} <= names


def test_source_final_character_clamp_is_explicit(client):
    http, state, _ = client
    lines = ['#' + 'x' * 599 for _ in range(20)]
    lines[5] = 'import b.service' + ' ' * (600 - len('import b.service'))
    (state.root / 'a/main.py').write_text('\n'.join(lines))
    run = create(http)
    base = '/api/projects/p/architecture-checks/' + run['runId']
    finding = http.get(base, params={'collection': 'findings'}).json()['items'][0]
    source = http.post(base + '/findings/' + finding['id'] + '/source', json={'edgeIndex': 0}).json()
    assert len(source['text']) == 12000 and source['truncated'] is True
    assert source['startLine'] == 1


def test_cursor_replacement_cannot_exceed_exact_json_budget(client):
    _, _, service = client
    with pytest.raises(StoreError) as error:
        service._bounded_page({}, [{'text': 'x' * 63895}, {'text': 'y' * 1000}], 10000, 10002)
    assert error.value.status == 413
    page = service._bounded_page({}, [{'text': 'x' * 63894}, {'text': 'y' * 1000}], 10000, 10002)
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 64000
    assert page['nextOffset'] == 10001 and len(page['items']) == 1


from test_agent_mcp import atlas_server


def test_real_stdio_architecture_roundtrip(atlas_server, tmp_path):
    """SDK stdio→MCP→настоящий HTTP→общий движок→proof/source без подмены транспорта."""
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    fixture(tmp_path)
    port, _ = atlas_server
    def unpack(result):
        assert not result.isError, result
        return result.structuredContent or json.loads(result.content[0].text)
    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=['-m', 'atlas.agent_mcp', '--port', str(port), '--project-id', 'legacy-fixture'])
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = unpack(await session.call_tool('run_architecture_checks', {'snapshotId': 'first', 'rules': config().model_dump()}))
                assert result['status'] == 'violated'
                findings = unpack(await session.call_tool('read_architecture_checks', {'runId': result['runId'], 'collection': 'findings'}))
                proof = unpack(await session.call_tool('read_architecture_evidence', {'runId': result['runId'], 'findingId': findings['items'][0]['id']}))
                assert proof['items'][0]['target'] == 'b/service.py'
                source = unpack(await session.call_tool('read_architecture_source', {'runId': result['runId'], 'findingId': findings['items'][0]['id'], 'edgeIndex': 0}))
                assert source['sourceByteHash'] == proof['items'][0]['sourceHash']
                assert source['text'] == 'from b.service import work'
    asyncio.run(scenario())
