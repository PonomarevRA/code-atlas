"""Реальный stdio MCP-прокси взаимодействует с отдельным loopback Atlas."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from atlas import store
from test_grouping_cache import legacy_store
from agent_view_fixture import view_graph


@pytest.fixture
def atlas_server(tmp_path):
    """Поднимает Atlas на временном порту и гарантированно завершает процесс."""
    state,_,path,_=legacy_store(tmp_path)
    graph = view_graph()
    next(node for node in graph['nodes'] if node['id']=='a')['testFacts'] = {
        'label':'Invoice smoke scenario', 'framework':'xunit', 'language':'csharp',
        'modId':'framework-xunit', 'tags':['agent-contract-tag'], 'runtimeStatus':'unknown',
        'sourceKind':'snapshot-captured-source', 'dataSources':[{'id':'inline-1','kind':'inline-data',
            'declaredRowCount':3,'nodeIds':['b'], 'rows':[{'id':'row-1','index':0,'file':'Service.cs','line':4,
            'bindings':[{'name':'value','index':0,'value':{'kind':'null','expression':'null'}}]}], 'rowsTruncated':True}]}
    next(node for node in graph['nodes'] if node['id']=='a').update(comment='Возвращает итог расходов.', commentScope='declaration',
                             implementationComment='Считает расходы по операциям.', implementationName='Calculator',
                             commentSource={'sourceKind':'snapshot-captured-source','sourceHash':'captured'})
    graph['nodes'].append({'id': 'attr:fixture', 'kind': 'attribute', 'label': 'SnapshotMarker',
                          'text': 'SnapshotMarker("captured")', 'scope': 'declaration',
                          'ownerNodeId': 'a', 'file': 'Service.cs', 'line': 4,
                          'sourceKind': 'snapshot-captured-source'})
    graph['edges'].append({'source': 'a', 'target': 'attr:fixture', 'relation': 'has_attribute'})
    store.write_json(path,graph)
    (tmp_path / 'health_fixture.py').write_text(
        'def double(value):\n    """Return twice the supplied value."""\n    return value * 2\n',
        encoding='utf-8')
    state.close()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
    output=(tmp_path/'server.log').open('w')
    process=subprocess.Popen([sys.executable,'-m','atlas.cli','--port',str(port),'--data-dir',str(tmp_path/'state')],stdout=output,stderr=output)
    base=f'http://127.0.0.1:{port}'
    try:
        with httpx.Client(trust_env=False,timeout=.3) as client:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((tmp_path/'server.log').read_text())
                try:
                    if client.get(base+'/api/health').status_code==200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            else:
                pytest.fail('Atlas did not become ready')
        yield port,base
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill();process.wait()
        output.close()


def unpack(result):
    """Читает structuredContent реального SDK, сохраняя совместимость JSON textblock."""
    assert not result.isError,result
    if result.structuredContent:
        return result.structuredContent
    return json.loads(result.content[0].text)


def test_agent_mcp_eof_keeps_process_standard_handles_open():
    """SDK transport finalizers must leave handles usable for frozen-process shutdown."""
    script = (
        'import atlas.agent_mcp, gc, sys; atlas.agent_mcp.main(); gc.collect(); '
        'print("stdout remains open"); print("stderr remains open", file=sys.stderr)'
    )
    result = subprocess.run([sys.executable, '-c', script], input='', capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'stdout remains open\n'
    assert result.stderr == 'stderr remains open\n'


def test_agent_mcp_real_stdio_create_and_get(atlas_server):
    """Клиент ищет raw-ID, сохраняет заметку и получает те же факты через stdio и HTTP."""
    port,base=atlas_server
    async def scenario():
        """Выполняет полный MCP handshake и инструменты без прямого вызова Python функций."""
        parameters=StdioServerParameters(command=sys.executable,args=['-m','atlas.agent_mcp','--port',str(port)],env={**os.environ,'HTTP_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':''})
        async with stdio_client(parameters) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                catalog=await session.list_tools()
                tools={tool.name:tool for tool in catalog.tools}
                from atlas.agent_setup import TOOLS
                assert set(tools) == set(TOOLS)
                settings = unpack(await session.call_tool('inspect_semantic_engine_settings', {}))
                assert settings['configuration']['scope'] == 'host-global'
                assert tools['set_semantic_engine_paths'].annotations.readOnlyHint is False
                diagnostic = unpack(await session.call_tool('inspect_semantic_engine_settings', {
                    'modId': 'semantic-python', 'projectId': 'legacy-fixture'}))
                assert diagnostic['candidateStatus'] == 'presence-only'
                assert diagnostic['projectId'] == 'legacy-fixture'
                saved = unpack(await session.call_tool('set_semantic_engine_paths', {
                    'modId': 'semantic-python', 'paths': {'pythonPath': '/missing/explicit-python'}}))
                assert saved['paths']['pythonPath'] == '/missing/explicit-python'
                reset = unpack(await session.call_tool('set_semantic_engine_paths', {
                    'modId': 'semantic-python', 'paths': {'pythonPath': ''}}))
                assert reset['mode'] == 'automatic'
                assert 'get_test_coverage' in tools
                schema = tools['plan_code_changes'].inputSchema
                projection_array = next(item for item in schema['properties']['projections']['anyOf'] if item.get('type') == 'array')
                projection_schema = schema['$defs'][projection_array['items']['$ref'].split('/')[-1]]
                assert projection_schema['required'] == ['identity', 'reason']
                assert projection_schema['additionalProperties'] is False
                assert 'healthIdentity' in projection_schema['properties']['identity']['description']
                assert projection_array['maxItems'] == 120
                draft_array = next(item for item in schema['properties']['drafts']['anyOf'] if item.get('type') == 'array')
                draft_schema = schema['$defs'][draft_array['items']['$ref'].split('/')[-1]]
                assert draft_schema['required'] == ['file', 'content'] and draft_array['maxItems'] == 20
                baseline=unpack(await session.call_tool('get_code_health', {'projectId':'legacy-fixture'}))
                assert baseline['assessmentKind']=='measured' and baseline['status']=='unavailable'
                invalid_plan = await session.call_tool('plan_code_changes', {
                    'projectId': 'legacy-fixture', 'baselineAssessmentId': baseline['assessmentId'],
                    'intent': 'Review file changes', 'projections': [
                        {'file': 'web/progressive-placement.js', 'change': 'Use visible card footprints'},
                        {'file': 'web/method-sequence-renderer.js', 'change': 'Separate actions from text'}]})
                assert invalid_plan.isError
                invalid_text = '\n'.join(block.text for block in invalid_plan.content if block.type == 'text')
                assert 'healthIdentity' in invalid_text and 'drafts=' in invalid_text
                assert 'Field required' not in invalid_text
                measured = unpack(await session.call_tool('get_code_health', {
                    'projectId': 'legacy-fixture', 'sourceMode': 'working-tree', 'files': ['health_fixture.py']}))
                assert measured['nodes']
                scenario = unpack(await session.call_tool('plan_code_changes', {
                    'projectId': 'legacy-fixture', 'baselineAssessmentId': measured['assessmentId'],
                    'intent': 'Plan an existing method', 'projections': [{
                        'identity': measured['nodes'][0]['healthIdentity'], 'reason': 'Preserve the existing contract'}]}))
                assert scenario['assessmentKind'] == 'estimated' and scenario['basis'] == 'scenario-assumptions'
                forecast=unpack(await session.call_tool('plan_code_changes', {'projectId':'legacy-fixture','baselineAssessmentId':baseline['assessmentId'],'intent':'Preview a new helper','drafts':[{'file':'draft.py','content':'def run(x):\n    return x * 2\n'}]}))
                assert forecast['assessmentKind']=='estimated' and forecast['projections']
                compared=unpack(await session.call_tool('compare_code_health', {'projectId':'legacy-fixture','beforeAssessmentId':baseline['assessmentId'],'afterAssessmentId':baseline['assessmentId'],'planId':forecast['planId']}))
                assert compared['verdict']=='UNKNOWN'
                report=unpack(await session.call_tool('get_code_health_report', {'projectId':'legacy-fixture'}))
                assert report['sourceMode']=='snapshot'
                assert tools['get_code_health'].annotations.readOnlyHint is False
                assert tools['get_test_coverage'].annotations.readOnlyHint is True
                assert tools['create_view'].annotations.readOnlyHint is False
                projects=unpack(await session.call_tool('list_projects',{}))
                assert projects['projects'][0]['id']=='legacy-fixture'
                found=unpack(await session.call_tool('search_nodes',{'projectId':'legacy-fixture','query':'Handle'}))
                assert found['snapshotId']=='first'
                assert next(node for node in found['nodes'] if node['id']=='a')['comment']=='Возвращает итог расходов.'
                metadata_found=unpack(await session.call_tool('search_nodes',{'projectId':'legacy-fixture','query':'agent-contract-tag'}))
                assert metadata_found['nodes'][0]['id']=='a'
                assert metadata_found['nodes'][0]['analysisFacts']['test']['framework']=='xunit'
                explained=unpack(await session.call_tool('get_node', {'projectId':'legacy-fixture','snapshotId':'first','nodeId':'a'}))
                assert explained['node']['implementationComment']=='Считает расходы по операциям.'
                assert explained['node']['testFacts']['dataSources'][0]['declaredRowCount']==3
                datasets = unpack(await session.call_tool('get_test_data', {
                    'projectId':'legacy-fixture','snapshotId':'first','nodeId':'a'}))
                assert datasets['sources'][0]['id'] == 'inline-1' and datasets['rows'] == []
                declared = unpack(await session.call_tool('get_test_data', {
                    'projectId':'legacy-fixture','snapshotId':'first','nodeId':'a','sourceId':'inline-1'}))
                assert declared['rows'][0]['bindings'][0]['value']['kind'] == 'null'
                assert declared['captureTruncated'] and declared['runtimeStatus'] == 'unknown'
                assert tools['get_test_data'].annotations.readOnlyHint is True
                assert (await session.call_tool('get_test_data', {
                    'projectId':'legacy-fixture','snapshotId':'old','nodeId':'a'})).isError
                attribute=unpack(await session.call_tool('get_node', {'projectId':'legacy-fixture','snapshotId':'first','nodeId':'attr:fixture'}))
                assert attribute['node']['text']=='SnapshotMarker("captured")'
                neighbors=unpack(await session.call_tool('get_neighbors', {'projectId':'legacy-fixture','snapshotId':'first','nodeId':'a','direction':'outgoing'}))
                assert any(edge['target']=='attr:fixture' and edge['relation']=='has_attribute' for edge in neighbors['edges'])
                assert neighbors['nodes'][0]['comment']=='Возвращает итог расходов.'
                stale=await session.call_tool('get_node', {'projectId':'legacy-fixture','snapshotId':'old','nodeId':'attr:fixture'})
                assert stale.isError
                created=unpack(await session.call_tool('create_view',{'projectId':'legacy-fixture','snapshotId':'first','title':'Agent view','nodeIds':['b'],'excludeKinds':['dto'],'depth':1,'annotations':[{'id':'n','title':'Agent reasoning','text':'Author explanation','nodeIds':['b']}]}))
                assert created['nodeCount']==3 and created['edgeCount']==4
                assert created['url']==base+'/view.html?id='+created['id']
                fetched=unpack(await session.call_tool('get_view',{'viewId':created['id']}))
                assert {node['id'] for node in fetched['nodes']}=={'a','b','c'}
                assert fetched['annotations'][0]['text']=='Author explanation'
                assert next(node for node in fetched['nodes'] if node['id']=='a')['comment']=='Возвращает итог расходов.'
                rejected=await session.call_tool('create_view',{'projectId':'legacy-fixture','snapshotId':'old','title':'stale','nodeIds':['b']})
                assert rejected.isError
                with httpx.Client(trust_env=False) as client:
                    saved=client.get(base+'/api/views/'+created['id']).json()
                    assert saved['annotations']==fetched['annotations']
                    assert len(client.get(base+'/api/views').json()['views'])==1
    asyncio.run(scenario())


def test_agent_mcp_config_is_port_scoped_stdio():
    """CLI печатает самостоятельную конфигурацию без запуска индексации/сервера."""
    result=subprocess.run([sys.executable,'-m','atlas.cli','--agent-mcp-config','--port','8877'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    config=json.loads(result.stdout)['mcpServers']['code-atlas-agent']
    assert config['command']==sys.executable
    assert config['args']==['-m','atlas.agent_mcp','--port','8877']
    assert Path(config['env']['PYTHONPATH']).is_dir()


def test_agent_mcp_real_scoped_stdio_uses_selected_project(atlas_server):
    """Настройка --project-id работает в отдельном процессе; другой projectId отвергается."""
    port,_=atlas_server
    async def scenario():
        """Проверяет defaults инструмента после настоящего MCP handshake."""
        parameters=StdioServerParameters(command=sys.executable,args=['-m','atlas.agent_mcp','--port',str(port),'--project-id','legacy-fixture'])
        async with stdio_client(parameters) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                projects=unpack(await session.call_tool('list_projects',{}))
                assert [project['id'] for project in projects['projects']]==['legacy-fixture']
                found=unpack(await session.call_tool('search_nodes',{'query':'Handler'}))
                assert found['snapshotId']=='first'
                assert any(node['id']=='a' for node in found['nodes'])
                rejected=await session.call_tool('search_nodes',{'projectId':'other','query':'Handler'})
                assert rejected.isError
    asyncio.run(scenario())
