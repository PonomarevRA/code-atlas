"""HTTP/MCP preserve node and source fences when navigating lazy syntax pages."""
import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from atlas import server, store, agent_mcp
from test_control_flow import branch_project
from test_grouping_cache import legacy_store
from test_agent_mcp_scope import tool_data


def test_chunk_http_validates_scope_and_publication(branch_project, tmp_path, monkeypatch):
    """Stale requests never enter the AST reader; a publication race rejects its result."""
    from atlas import method_flow_chunks
    project, graph = branch_project
    state, project_id, path, _ = legacy_store(tmp_path)
    store.write_json(path, graph)
    state.projects[project_id]['path'] = str(project)
    store.write_json(state.registry, state.projects)
    state.close()
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        route = f'/api/projects/{project_id}/method-flow-chunk'
        body = {'snapshotId': path.parent.name, 'nodeId': 'run', 'limit': 2}
        result = client.post(route, json=body)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['analysisKind'] == 'syntax-outline'
        assert data['flowStatus'] == 'not-computed'
        assert len(data['children']) <= 2
        assert client.post(route, json={**body, 'path': '/etc/passwd'}).status_code == 422
        assert client.post(route, json={**body, 'limit': True}).status_code == 422
        original = method_flow_chunks.build_method_flow_chunk

        def forbidden(*args, **kwargs):
            """The snapshot check must happen before parsing source bytes."""
            raise AssertionError('stale snapshot entered AST reader')

        monkeypatch.setattr(method_flow_chunks, 'build_method_flow_chunk', forbidden)
        assert client.post(route, json={**body, 'snapshotId': 'old'}).status_code == 409

        def publish(*args, **kwargs):
            """A concurrent rebuild cannot make a page appear to belong to its successor."""
            data = original(*args, **kwargs)
            newer = path.parent.parent / 'newer' / 'atlas-graph.json'
            store.write_json(newer, graph)
            with client.app.state.store.lock:
                client.app.state.store.projects[project_id]['_graph'] = str(newer)
            return data

        monkeypatch.setattr(method_flow_chunks, 'build_method_flow_chunk', publish)
        assert client.post(route, json=body).status_code == 409


def test_chunk_mcp_preserves_refs_and_rejects_cross_project(monkeypatch):
    """The registered tool forwards only bounded identifiers through loopback HTTP."""
    calls = []

    def handler(request):
        """Observe the actual HTTP payload emitted by FastMCP's registered tool."""
        calls.append(request)
        assert request.url.path == '/api/projects/one/method-flow-chunk'
        assert json.loads(request.content) == {
            'snapshotId': 's', 'nodeId': 'n', 'chunkRef': 'opaque', 'limit': 3}
        return httpx.Response(200, json={'analysisKind': 'syntax-outline', 'nextRef': 'next'})

    original = httpx.AsyncClient
    monkeypatch.setattr(agent_mcp.httpx, 'AsyncClient',
                        lambda **opts: original(**opts, transport=httpx.MockTransport(handler)))

    async def scenario():
        """Exercise SDK validation and selected-project guards without reading the filesystem."""
        mcp = agent_mcp.create_mcp(8877, project_id='one')
        args = {'snapshotId': 's', 'nodeId': 'n', 'chunkRef': 'opaque', 'limit': 3}
        result = tool_data(await mcp.call_tool('get_method_flow_chunk', args))
        assert result['nextRef'] == 'next'
        try:
            await mcp.call_tool('get_method_flow_chunk', {**args, 'projectId': 'two'})
        except Exception:
            pass
        else:
            raise AssertionError('cross-project request accepted')
        assert len(calls) == 1

    asyncio.run(scenario())
