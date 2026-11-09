"""Actual HTTP and MCP registrations preserve task scope, source fences and continuation."""

import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from atlas import agent_mcp, server, store
from atlas.analysis_contexts import AnalysisContexts, AnalysisContextError
from atlas.analysis_section_service import read_section
from atlas.api_models import AnalysisSectionInput
import pytest
from test_control_flow import branch_project
from test_grouping_cache import legacy_store
from test_agent_mcp_scope import tool_data


def prepare_state(tmp_path, branch_project):
    """Publish one real C# source fixture through the legacy-compatible storage boundary."""
    project, graph = branch_project
    state, project_id, path, _ = legacy_store(tmp_path)
    store.write_json(path, graph)
    state.projects[project_id]['path'] = str(project)
    store.write_json(state.registry, state.projects)
    state.close()
    return project, project_id, path


def test_context_http_reuses_parse_and_old_refs_reject_new_source(tmp_path, branch_project, monkeypatch):
    """Every source is still checked, while repeated valid syntax pages share one actual parser call."""
    from atlas import method_flow_chunks
    project, project_id, path = prepare_state(tmp_path, branch_project)
    calls, original = [], method_flow_chunks._parse

    def counted(*args):
        """Count native parse entry rather than claiming all source reading was eliminated."""
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(method_flow_chunks, '_parse', counted)
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        base = f'/api/projects/{project_id}'
        opened = client.post(base + '/analysis-contexts', json={'snapshotId': 'first'})
        assert opened.status_code == 200
        context_id = opened.json()['contextId']
        body = {'snapshotId': 'first', 'nodeId': 'run', 'contextId': context_id}
        first = client.post(base + '/method-flow-chunk', json=body)
        assert first.status_code == 200 and first.json()['available']
        reference = first.json()['currentRef']
        for _ in range(3):
            response = client.post(base + '/method-flow-chunk', json={**body, 'chunkRef': reference})
            assert response.status_code == 200
            assert response.json()['parseResidency'] == 'task-context-idle-cache'
        assert calls == [1]
        assert client.post(base + '/method-flow-chunk', json={**body, 'contextId': 'missing'}).status_code == 410
        source = project / 'Handler.cs'
        source.write_text(source.read_text().replace('Accept();', 'Updated();'))
        assert client.post(base + '/method-flow-chunk', json={**body, 'chunkRef': reference}).status_code == 409
        assert client.post(base + '/analysis-contexts/' + context_id + '/close',
                           json={'snapshotId': 'first'}).status_code == 200
        assert client.post(base + '/method-flow-chunk', json=body).status_code == 410


def test_http_section_details_are_navigable_and_scope_capacity_is_controlled(tmp_path, branch_project, monkeypatch):
    """The registered section endpoint exposes real parsed steps and clear scope/capacity errors."""
    prepare_state(tmp_path, branch_project)
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        base = '/api/projects/legacy-fixture'
        context_id = client.post(base + '/analysis-contexts', json={'snapshotId': 'first'}).json()['contextId']
        body = {'snapshotId': 'first', 'nodeId': 'run', 'contextId': context_id, 'section': 'methodFlow'}
        description = client.post(base + '/node-description', json={'snapshotId': 'first', 'nodeId': 'run'}).json()
        assert description['sectionExploration']['toolName'] == 'get_analysis_section'
        first = client.post(base + '/analysis-section', json=body)
        assert first.status_code == 200, first.text
        data = first.json()
        steps = next(item for item in data['items'] if item['key'] == 'steps')
        page = client.post(base + '/analysis-section', json={**body, 'sectionRef': steps['ref']})
        assert page.status_code == 200 and page.json()['items']
        assert page.json()['coverage']['analysisLimitsUnchanged']
        assert client.post(base + '/analysis-section', json={**body, 'section': 'documentation',
                           'sectionRef': steps['ref']}).status_code == 409
        assert client.post(base + '/analysis-section', json={**body, 'limit': True}).status_code == 422
        def forbidden(*args):
            """Opening, rejecting or closing a scope must never clone the full published graph."""
            raise AssertionError('Context open called comparison_snapshot')

        monkeypatch.setattr(client.app.state.store, 'comparison_snapshot', forbidden)
        assert client.post(base + '/analysis-contexts', json={'snapshotId': 'stale'}).status_code == 409
        for _ in range(7):
            assert client.post(base + '/analysis-contexts', json={'snapshotId': 'first'}).status_code == 200
        assert client.post(base + '/analysis-contexts', json={'snapshotId': 'first'}).status_code == 429


def test_new_mcp_tools_forward_context_and_continuations(monkeypatch):
    """FastMCP registration preserves selectors through loopback HTTP and project guards."""
    calls = []

    def handler(request):
        """Return bounded fake server responses while recording the real SDK transport payload."""
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'ok': True})

    original = httpx.AsyncClient
    monkeypatch.setattr(agent_mcp.httpx, 'AsyncClient',
                        lambda **opts: original(**opts, transport=httpx.MockTransport(handler)))

    async def scenario():
        """Use registered tools rather than invoking the closures directly."""
        mcp = agent_mcp.create_mcp(8877, project_id='p')
        examples = [
            ('open_analysis_context', {'snapshotId': 's'}),
            ('get_neighbors', {'snapshotId': 's', 'nodeId': 'n', 'cursor': 'next'}),
            ('get_method_flow_chunk', {'snapshotId': 's', 'nodeId': 'n', 'contextId': 'c'}),
            ('get_analysis_section', {'snapshotId': 's', 'nodeId': 'n', 'contextId': 'c', 'sectionRef': 'ref'}),
            ('close_analysis_context', {'snapshotId': 's', 'contextId': 'c'}),
        ]
        for tool, arguments in examples:
            assert tool_data(await mcp.call_tool(tool, arguments))['ok']

    asyncio.run(scenario())
    assert calls[1][1]['cursor'] == 'next'
    assert calls[2][1]['contextId'] == 'c'
    assert calls[3][1]['sectionRef'] == 'ref'
    assert calls[4][0].endswith('/analysis-contexts/c/close')


def test_section_cursor_rejects_new_semantic_overlay_with_same_source(tmp_path):
    """A cached description can refresh its semantic facts without accepting an old page position."""
    (tmp_path / 'a.py').write_text('def Run():\n    return 1\n')

    class State:
        """Expose a mutable semantic revision while snapshot and source bytes remain fixed."""
        revision = 'old'

        def source_node_snapshot(self, *args):
            """Return independent raw facts in the public narrow-reader shape."""
            return tmp_path, {'nodes': [{'id': 'n', 'file': 'a.py', 'line': 1, 'label': 'Run()', 'kind': 'method'}],
                              'metadata': {'semanticRevision': self.revision}}

        def comparison_snapshot(self, *args):
            """Return the full builder scope with the effective overlay identity."""
            return tmp_path, {'metadata': {'semanticEnrichment': {'revision': self.revision}}}

        def assert_snapshot(self, *args):
            """The snapshot identity deliberately stays fixed throughout this regression."""

    state = State()
    contexts = AnalysisContexts()
    identifier = contexts.open('p', 's')['contextId']
    body = AnalysisSectionInput(snapshotId='s', nodeId='n', contextId=identifier, limit=1)

    def builder(*args):
        """Use distinct facts to make silent cross-revision continuation observable."""
        return {'methodFlow': [{'label': state.revision + '-a'}, {'label': state.revision + '-b'}]}

    first = read_section(state, contexts, 'p', body, builder, lambda _: '')
    assert first['nextRef']
    state.revision = 'new'
    body.sectionRef = first['nextRef']
    with pytest.raises(AnalysisContextError) as error:
        read_section(state, contexts, 'p', body, builder, lambda _: '')
    assert error.value.status == 409


def test_cross_file_description_change_refreshes_facts_and_invalidates_cursor(tmp_path):
    """Unchanged selected bytes cannot mask handler/config facts read from another file."""
    (tmp_path / 'a.py').write_text('def Run():\n    return 1\n')
    related = tmp_path / 'handler.py'
    related.write_text('old-handler')

    class State:
        """Keep source/snapshot/semantic identity fixed while a dependency changes."""
        def source_node_snapshot(self, *args):
            """Publish only the selected safe reader location and scalar scope identity."""
            return tmp_path, {'nodes': [{'id': 'n', 'file': 'a.py', 'line': 1, 'label': 'Run()', 'kind': 'method'}],
                              'metadata': {'semanticRevision': ''}}

        def comparison_snapshot(self, *args):
            """The actual builder may consult more files than source_node_snapshot returns."""
            return tmp_path, {'metadata': {}}

        def assert_snapshot(self, *args):
            """No snapshot publication occurs in this dependency-only regression."""

    contexts = AnalysisContexts()
    identifier = contexts.open('p', 's')['contextId']
    body = AnalysisSectionInput(snapshotId='s', nodeId='n', contextId=identifier, section='calls', limit=1)

    def builder(*args):
        """Model working-tree infrastructure resolution against a separately owned dependency."""
        return {'calls': [{'target': related.read_text() + '-a'}, {'target': related.read_text() + '-b'}]}

    state = State()
    first = read_section(state, contexts, 'p', body, builder, lambda _: '')
    assert first['nextRef']
    related.write_text('new-handler')
    body.sectionRef = first['nextRef']
    with pytest.raises(AnalysisContextError) as error:
        read_section(state, contexts, 'p', body, builder, lambda _: '')
    assert error.value.status == 409
    body.sectionRef = None
    fresh = read_section(state, contexts, 'p', body, builder, lambda _: '')
    assert fresh['items'][0]['ref'] != first['items'][0]['ref']
    body.sectionRef = fresh['items'][0]['ref']
    leaf = read_section(state, contexts, 'p', body, builder, lambda _: '')
    assert leaf['items'][0]['value'] == 'new-handler-a'
