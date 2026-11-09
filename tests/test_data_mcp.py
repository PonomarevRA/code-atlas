"""Real stdio tools return dated snapshots and refresh only on explicit requests."""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_agent_mcp import atlas_server, unpack


def test_data_mcp_stdio_dates_and_explicit_refresh(atlas_server,tmp_path):
    """An agent can read a stale snapshot and choose when to call ensure_data_map."""
    port,_=atlas_server
    root=tmp_path/'data';root.mkdir();source=root/'a.csv';source.write_text('id\n001\n')
    async def scenario():
        parameters=StdioServerParameters(command=sys.executable,args=['-m','atlas.agent_mcp','--port',str(port)])
        async with stdio_client(parameters) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools={tool.name:tool for tool in (await session.list_tools()).tools}
                assert tools['query_data_records'].annotations.readOnlyHint is True
                assert tools['ensure_data_map'].annotations.readOnlyHint is False
                files=unpack(await session.call_tool('discover_data_files',{'root':str(root)}))
                assert files['built'] is False
                assert unpack(await session.call_tool('list_data_maps',{}))=={'maps':[]}
                created=unpack(await session.call_tool('create_data_map',{'root':str(root),'files':['a.csv']}))
                map_id=created['map']['id'];job_id=created['job']['id']
                for _ in range(100):
                    job=unpack(await session.call_tool('get_data_job',{'jobId':job_id}))
                    if job['status'] not in {'queued','running'}:break
                    await asyncio.sleep(.03)
                assert job['status']=='completed',job
                overview=unpack(await session.call_tool('get_data_overview',{'mapId':map_id}))
                args={'mapId':map_id,'snapshotId':overview['snapshotId']}
                schema=unpack(await session.call_tool('get_data_schema',args))
                collection=next(item for item in schema['items'] if item['kind']=='collection')
                ranked=unpack(await session.call_tool('rank_data_keys',args))
                key=ranked['items'][0]
                values=unpack(await session.call_tool('get_data_key_values',{**args,'keyId':key['id']}))
                path=unpack(await session.call_tool('get_data_key_path',{**args,'keyId':key['id'],
                    'valueRecordId':values['items'][0]['valueRecordId']}))
                assert path['matchedRecords']==1 and len(path['nodes'])==3
                assert path['builtAt']==overview['builtAt']
                assert all(tools[name].annotations.readOnlyHint for name in ['rank_data_keys','get_data_key_values','get_data_key_path'])
                source.write_text('id\n002\n')
                stale=unpack(await session.call_tool('query_data_records',{**args,'collectionId':collection['id']}))
                assert stale['items'][0]['values']['id']=='001'
                assert stale['builtAt']==overview['builtAt'] and stale['freshness']['status']=='stale'
                assert unpack(await session.call_tool('get_data_job',{'jobId':job_id}))['status']=='completed'
                updated=unpack(await session.call_tool('ensure_data_map',{'mapId':map_id}))
                assert updated['job']['id']!=job_id
                for _ in range(100):
                    job=unpack(await session.call_tool('get_data_job',{'jobId':updated['job']['id']}))
                    if job['status'] not in {'queued','running'}:break
                    await asyncio.sleep(.03)
                assert job['status']=='completed'
                rejected=await session.call_tool('get_data_schema',args)
                assert rejected.isError
                return map_id, job['id']
    map_id, completed_job_id=asyncio.run(scenario())
    async def scoped():
        parameters=StdioServerParameters(command=sys.executable,args=['-m','atlas.agent_mcp','--port',str(port),'--data-map-id',map_id])
        async with stdio_client(parameters) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                assert unpack(await session.call_tool('get_data_overview',{}))['id']==map_id
                assert (await session.call_tool('ensure_data_map',{'mapId':'another'})).isError
                assert (await session.call_tool('discover_data_files',{'root':str(root)})).isError
                assert unpack(await session.call_tool('get_map_job',{'jobId':completed_job_id}))['mapId']==map_id
                assert unpack(await session.call_tool('cancel_map_job',{'jobId':completed_job_id}))['mapId']==map_id
                assert (await session.call_tool('get_map_job',{'jobId':completed_job_id,'mapId':'another'})).isError
                assert (await session.call_tool('cancel_map_job',{'jobId':completed_job_id,'mapId':'another'})).isError
    asyncio.run(scoped())


def test_project_bound_data_tools_reject_foreign_map_before_mutation(monkeypatch):
    """Metadata may be checked, but a foreign map never receives POST refresh/cancel."""
    import httpx
    import pytest
    from atlas import agent_mcp
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path=='/api/data-maps/foreign':
            return httpx.Response(200,json={'id':'foreign','projectId':'two'})
        raise AssertionError('Unexpected mutation: '+request.method+' '+request.url.path)
    original=httpx.AsyncClient
    monkeypatch.setattr(agent_mcp.httpx,'AsyncClient',lambda **options:original(**options,transport=httpx.MockTransport(handler)))
    async def scenario():
        server=agent_mcp.create_mcp(8877,project_id='one')
        with pytest.raises(Exception,match='не относится'):
            await server.call_tool('ensure_data_map',{'mapId':'foreign'})
        with pytest.raises(Exception,match='не относится'):
            await server.call_tool('get_data_schema',{'mapId':'foreign','snapshotId':'s'})
        assert all(request.method=='GET' for request in calls)
        count=len(calls)
        with pytest.raises(Exception):
            await server.call_tool('create_data_map',{'files':['a.csv'],'projectId':'two'})
        assert len(calls)==count
    asyncio.run(scenario())
