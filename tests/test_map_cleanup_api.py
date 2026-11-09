"""
Public cleanup transport refuses paths/implicit confirmation and preserves idempotent exact-plan status.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from atlas.map_cleanup_api import register_map_cleanup_routes
from test_map_cleanup import fixture, PROJECT, wait_done


def test_bounded_schema_explicit_confirmation_and_same_plan_status(fixture):
    """
    Validate actual HTTP DTO/error/replay behavior against an isolated real snapshot service.

    Args:
        fixture: Private synthetic map service; no real map is ever confirmed by this test.
    """
    code,data,cleanup,source,directory=fixture
    app=FastAPI();register_map_cleanup_routes(app,cleanup)
    base='/api/technical/map-cleanup'
    with TestClient(app) as client:
        for maps in [[],[{'kind':'docs','id':PROJECT}],[{'kind':'code','id':'../escape'}],
                     [{'kind':'code','id':PROJECT,'path':'/private/foreign'}],
                     [{'kind':'code','id':str(i)} for i in range(51)]]:
            assert client.post(base+'/preview',json={'maps':maps}).status_code==422
        response=client.post(base+'/preview',json={'maps':[{'kind':'code','id':PROJECT}]})
        assert response.status_code==200
        plan=response.json();path=base+'/'+plan['planId']
        for body in [{},{'confirm':False},{'confirm':1},{'confirm':'true'},{'confirm':True,'paths':['/foreign']}]:
            assert client.post(path+'/execute',json=body).status_code==422
        assert directory.exists()
        receipt=client.post(path+'/execute',json={'confirm':True})
        assert receipt.status_code==200
        final=wait_done(cleanup,plan['planId'])
        assert client.get(path).json()==final
        assert client.post(path+'/execute',json={'confirm':True}).json()==final
        assert final['maps']==plan['maps'] and final['preserved']==plan['preserved']
        assert final['schemaVersion']=='atlas-map-cleanup-v1' and final['mapsCleared'] is True
        assert client.get(base+'/not-a-plan').status_code==404
        assert client.get(base).json()['operations']==[]
