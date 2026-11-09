"""Ограничение MCP выбранным проектом проверяется до передачи изменяющих запросов."""
import asyncio
import json
import httpx
import pytest
from atlas import agent_mcp
from test_control_flow import branch_project


def tool_data(result):
    """Возвращает structured result FastMCP независимо от пары text/structured."""
    if isinstance(result,dict):
        return result
    if isinstance(result,tuple) and len(result)==2 and isinstance(result[1],dict):
        return result[1]
    return json.loads(result[0].text)


def test_project_scoped_tools_reject_cross_project_before_http(monkeypatch):
    """Search/describe/create не уходят в другой проект; get_view не раскрывает чужой JSON."""
    calls=[]
    def handler(request):
        """Локальный transport позволяет видеть каждый фактический HTTP request."""
        calls.append(request)
        if request.url.path=='/api/projects':
            return httpx.Response(200,json={'projects':[{'id':'one','name':'One'},{'id':'two','name':'Two'}]})
        if request.url.path.endswith('/search-nodes'):
            return httpx.Response(200,json={'nodes':[{'id':'node','label':'N'}],'snapshotId':'s','total':1,'truncated':False})
        if request.url.path.startswith('/api/views/'):
            return httpx.Response(200,json={'projectId':'two','nodes':[{'label':'CROSS_PROJECT_SECRET'}]})
        raise AssertionError('Unexpected HTTP call '+request.url.path)
    original=httpx.AsyncClient
    def client(**options):
        """Применяет fake transport, проверяя отключение proxy/redirect в реальном прокси."""
        assert options['trust_env'] is False and options['follow_redirects'] is False
        return original(**options,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(agent_mcp.httpx,'AsyncClient',client)
    async def scenario():
        """Вызывает зарегистрированные SDK tools, а не приватные guard-функции."""
        mcp=agent_mcp.create_mcp(8877,project_id='one')
        projects=tool_data(await mcp.call_tool('list_projects',{}))
        assert [project['id'] for project in projects['projects']]==['one']
        found=tool_data(await mcp.call_tool('search_nodes',{'projectId':'one','query':'N'}))
        assert found['nodes'][0]['id']=='node'
        count=len(calls)
        for name,args in [('search_nodes',{'query':'N'}),('describe_node',{'snapshotId':'s','nodeId':'n'}),('get_node',{'snapshotId':'s','nodeId':'n'}),('get_neighbors',{'snapshotId':'s','nodeId':'n'}),('get_test_coverage',{'snapshotId':'s'}),('create_view',{'snapshotId':'s','title':'Cross','nodeIds':['n']}),('search_documentation',{'snapshotId':'s','query':'currency'}),('index_documentation',{'snapshotId':'s'}),('get_documentation_index',{'snapshotId':'s'})]:
            with pytest.raises(Exception):
                await mcp.call_tool(name,{'projectId':'two',**args})
            assert len(calls)==count
        with pytest.raises(Exception) as denied:
            await mcp.call_tool('get_view',{'viewId':'foreign'})
        assert 'CROSS_PROJECT_SECRET' not in str(denied.value)
        assert len(calls)==count+1
    asyncio.run(scenario())


@pytest.mark.parametrize('oversized', [False, True])
def test_describe_node_preserves_conditional_source_facts(branch_project, monkeypatch, oversized):
    """Зарегистрированный MCP tool передаёт AST arms, выражения и версию без потери вложенности."""
    from atlas.node_description import build_node_description
    project, graph = branch_project
    (project / 'Handler.cs').write_text('class Handler{int Run(int x){return x > 0 ? A() : x < 0 ? B() : 0;}}')
    description = build_node_description(project, graph, 'run', 's')
    if oversized:
        description['documentation']['summary'] = 'x' * 70_000
    def handler(request):
        """Отдаёт настоящее описание через HTTP transport реального MCP-прокси."""
        assert request.url.path == '/api/projects/one/node-description'
        assert json.loads(request.content) == {'snapshotId':'s','nodeId':'run'}
        return httpx.Response(200, json=description)
    original = httpx.AsyncClient
    monkeypatch.setattr(agent_mcp.httpx, 'AsyncClient',
                        lambda **options: original(**options, transport=httpx.MockTransport(handler)))
    async def scenario():
        """Проверяет публичный FastMCP contract без сетевого сервера."""
        mcp = agent_mcp.create_mcp(8877, project_id='one')
        result = tool_data(await mcp.call_tool('describe_node', {'snapshotId':'s','nodeId':'run'}))
        if oversized:
            assert result['truncated'] is True
            assert len(json.dumps(result, ensure_ascii=False).encode('utf-8')) < 64_000
        else:
            assert result['controlFlow'] == description['controlFlow']
            assert result['controlFlow']['branches'][1]['arms'][1]['expression'] == '0'
        assert result['readabilityRecommendations'] == description['readabilityRecommendations']
        assert result['complexity'] == description['complexity']
        assert result['complexity']['maxNestingDepth'] == 2
        assert result['readabilityRecommendations']['items'][0]['kind'] == 'extract-return-result'
        assert result['sourceHash'] == description['sourceHash']
        assert result['analyzerVersion'] == 'csharp-structure-v8-constructor-bodies'
    asyncio.run(scenario())
