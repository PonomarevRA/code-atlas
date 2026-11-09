"""HTTP layer preserves explicit imports and the independent data catalog."""
from fastapi.testclient import TestClient
from atlas.server import create_app
from atlas.data_graph import schema_graph
from test_data_maps import build
import networkx as nx


def test_data_api_dates_stale_export_technical_and_project_discovery(tmp_path, monkeypatch):
    """Reads show freshness without creating new jobs or mutating code snapshots."""
    root=tmp_path/'repo';root.mkdir();source=root/'data.csv';source.write_text('id\n001\n')
    app=create_app(tmp_path/'state')
    app.state.store.projects['p']={'id':'p','name':'Project','path':str(root),'status':'completed'}
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        discovery=client.get('/api/projects/p/data-files').json()
        assert discovery['files'][0]['path']=='data.csv'
        assert client.get('/api/data-maps').json()=={'maps':[]}
        assert not app.state.data_maps.root.exists()
        overview=build(app.state.data_maps,root,['data.csv'],projectId='p')
        body={'snapshotId':overview['snapshotId']}
        schema=client.post('/api/data-maps/'+overview['id']+'/query/schema',json=body).json()
        assert schema['builtAt']==overview['builtAt']
        endpoint='/api/data-maps/'+overview['id']+'/query/'
        keys=client.post(endpoint+'keys',json=body).json()
        key_id=keys['items'][0]['id']
        values=client.post(endpoint+'key-values',json={**body,'keyId':key_id}).json()
        path=client.post(endpoint+'key-path',json={**body,'keyId':key_id,'valueRecordId':values['items'][0]['valueRecordId']}).json()
        assert path['matchedRecords']==1 and len(path['nodes'])==3
        assert path['builtAt']==overview['builtAt']
        source.write_text('id\n002\n')
        stale=client.post('/api/data-maps/'+overview['id']+'/query/schema',json=body)
        assert stale.status_code==200 and stale.json()['freshness']['status']=='stale'
        exported=client.get(f"/api/data-maps/{overview['id']}/export",params={'snapshotId':overview['snapshotId']}).json()
        graph=nx.node_link_graph(exported,edges='links')
        assert graph.number_of_nodes()==len(exported['nodes'])
        assert exported['graph']['builtAt']==overview['builtAt']
        technical=client.get('/api/technical/storage').json()
        assert technical['buildPolicy']=='explicit-request-only'
        assert technical['maps'][0]['sizeBytes']>source.stat().st_size
        assert technical['maps'][0]['builtAt']==overview['builtAt']
        assert technical['codeMaps'][0]['freshness']['status']=='not-checked'
        calls=[]
        monkeypatch.setattr('atlas.data_api.subprocess.Popen',lambda args,**kw:calls.append(args))
        assert client.post('/api/technical/open-storage',json={'mapId':overview['id']}).status_code==200
        assert calls[0][-1]==str(app.state.data_maps.directory(overview['id']))
        assert client.post('/api/technical/open-storage',json={'mapId':['bad']}).status_code==400
        assert client.post('/api/data-maps',json={'projectId':'p','root':'/wrong','files':['data.csv']}).status_code==403
        assert len(app.state.data_maps.jobs)==1
        assert client.get('/data.html').status_code==client.get('/technical.html').status_code==200
