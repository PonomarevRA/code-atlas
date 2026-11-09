"""HTTP-контракт автономных снимков и поиск raw-ID с проверкой версии."""
from fastapi.testclient import TestClient
from atlas import server,store
from agent_view_fixture import view_graph,view_request
from test_grouping_cache import legacy_store


def saved_app(tmp_path):
    """Публикует тестовый исходный snapshot без запуска индексатора."""
    state,_,path,_=legacy_store(tmp_path)
    store.write_json(path,view_graph())
    state.close()
    return server.create_app(tmp_path/'state')


def test_saved_views_api_validation_snapshot_and_persistence(tmp_path):
    """Сохранённый вид читается после удаления проекта; POST не принимает новые факты от клиента."""
    app=saved_app(tmp_path)
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        route='/api/views'
        assert client.post(route,json=view_request(snapshotId='old')).status_code==409
        assert client.post(route,json=view_request(projectId='unknown')).status_code==404
        for changes in [{'nodes':[]},{'edges':[]},{'layout':{}},{'maxNodes':True},{'depth':4}]:
            assert client.post(route,json=view_request(**changes)).status_code==422
        assert client.post(route,json=view_request(nodeIds=['synthetic-note'])).status_code==400
        result=client.post(route,json=view_request())
        assert result.status_code==200,result.text
        view=result.json()
        assert len(view['nodes'])==3 and len(view['edges'])==4
        assert client.get(route).json()['views'][0]['id']==view['id']
        app.state.store.projects.clear()
        assert client.get(route+'/'+view['id']).json()==view
        assert client.put(route+'/'+view['id'],json=view).status_code==405
    with TestClient(server.create_app(tmp_path/'state'),base_url='http://127.0.0.1:8765') as client:
        assert client.get('/api/views/'+view['id']).json()==view


def test_agent_search_returns_real_ids_and_exact_snapshot(tmp_path, monkeypatch):
    """Поиск ограничивает результат и не может использовать устаревший snapshot или исходный код."""
    app = saved_app(tmp_path)
    def forbidden_full_copy(*args, **kwargs):
        """Поиску нельзя возвращаться к полной копии опубликованной карты."""
        raise AssertionError('Search must not copy the entire snapshot')
    monkeypatch.setattr(app.state.store, 'comparison_snapshot', forbidden_full_copy)
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        route='/api/projects/legacy-fixture/search-nodes'
        response=client.post(route,json={'query':'order','snapshotId':'first','limit':1})
        assert response.status_code==200,response.text
        result=response.json()
        assert result['snapshotId']=='first' and result['total']==2 and result['truncated']
        assert {node['id'] for node in result['nodes']} <= {'a','d'}
        assert client.post(route,json={'query':'order','snapshotId':'old'}).status_code==409
        assert client.post(route,json={'query':'order','source':'secret'}).status_code==422
        excluded=client.post(route,json={'query':'order','excludeKinds':['DTO']}).json()
        assert [node['id'] for node in excluded['nodes']]==['a']
