"""HTTP контракты общего документного слоя: explicit jobs, pinned reads и scope."""
import time
import json

from fastapi.testclient import TestClient
from atlas.server import create_app


def wait(client, job_id):
    """Ожидает небольшой настоящий process import, не подменяя parser результата."""
    for _ in range(300):
        job = client.get('/api/map-jobs/' + job_id).json()
        if job.get('status') not in {'queued', 'running'}:
            assert job['status'] == 'completed', job
            return job
        time.sleep(.02)
    raise AssertionError('Document fixture job timed out')


def test_document_api_import_history_table_views_and_manual_off(tmp_path):
    """Карта читается после удаления оригинала; off блокирует новый импорт, не историю."""
    root = tmp_path / 'repo'; root.mkdir()
    source = root / 'guide.md'; source.write_text('# Guide\n\n| Item | Value |\n| --- | --- |\n| A | 42 |\n', encoding='utf-8')
    app = create_app(tmp_path / 'state')
    app.state.store.projects['p'] = {'id': 'p', 'name': 'Fixture', 'path': str(root), 'status': 'completed'}
    app.state.store.projects['other'] = {'id': 'other', 'name': 'Other', 'path': str(root), 'status': 'completed'}
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        found = client.post('/api/document-files', json={'projectId': 'p'}).json()
        assert found['built'] is False and found['files'][0]['path'] == 'guide.md'
        assert not app.state.documents.catalog_path.exists()
        response = client.post('/api/document-maps/ensure', json={'projectId': 'p', 'files': ['guide.md']})
        assert response.status_code == 202, response.text
        created = response.json(); map_id = created['map']['id']
        first = wait(client, created['job']['id']); snapshot = first['snapshotId']
        endpoint = '/api/document-maps/' + map_id
        args = {'snapshotId': snapshot, 'projectId': 'p'}
        tree = client.post(endpoint + '/query/tree', json=args).json()
        root_id = tree['items'][0]['id']
        children = client.post(endpoint + '/query/tree', json={**args, 'parentId': root_id}).json()
        assert any(item['kind'] == 'section' for item in children['items'])
        tables = client.post(endpoint + '/query/search', json={**args, 'query': 'table'}).json()
        graph = client.post(endpoint + '/query/graph', json=args).json()
        table = next(node for node in graph['nodes'] if node['kind'] == 'table')
        window = client.post(endpoint + '/query/table', json={**args, 'nodeId': table['id']}).json()
        assert any(cell['text'] == '42' for cell in window['cells'])
        saved = client.post(endpoint + '/views', json={**args, 'title': 'Fixture'}).json()
        assert client.get(endpoint + '/views/' + saved['id']).json()['snapshotId'] == snapshot
        assert client.post(endpoint + '/query/node', json=args).status_code == 400
        assert client.post(endpoint + '/query/tree', json={**args, 'rowStart': 0}).status_code == 400
        assert client.post(endpoint + '/query/tree', json={**args, 'projectId': 'other'}).status_code == 403
        assert client.post('/api/map-jobs/' + first['id'] + '/cancel', json={'projectId': 'other'}).status_code == 403
        source.write_text('# Changed\n', encoding='utf-8')
        updated = client.post('/api/document-maps/ensure', json={'mapId': map_id, 'projectId': 'p'}).json()
        second = wait(client, updated['job']['id'])
        assert second['snapshotId'] != snapshot
        assert client.post(endpoint + '/query/table', json={**args, 'nodeId': table['id']}).json()['cells']
        source.unlink()
        historical = client.post(endpoint + '/query/table', json={**args, 'nodeId': table['id']}).json()
        assert historical['freshness']['status'] == 'stale'
        assert historical['cells']
        app.state.store.mods.enable('document-markdown', None, False)
        source.write_text('# Disabled mod\n', encoding='utf-8')
        blocked = client.post('/api/document-maps/ensure', json={'root': str(root), 'files': ['guide.md']}).json()
        for _ in range(200):
            job = client.get('/api/map-jobs/' + blocked['job']['id']).json()
            if job['status'] not in {'queued', 'running'}:
                break
            time.sleep(.02)
        assert job['status'] == 'failed' and 'CAPABILITY_UNAVAILABLE' in str(job)
        assert client.post(endpoint + '/query/table', json={**args, 'nodeId': table['id']}).json()['cells']


def test_document_api_content_and_scoped_job_read(tmp_path):
    """Длинный текст читается страницами, query не запускает новый parser/job."""
    root = tmp_path / 'repo'; root.mkdir()
    (root / 'text.yaml').write_text('value: ' + 'x' * 15000, encoding='utf-8')
    app = create_app(tmp_path / 'state')
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        created = client.post('/api/document-maps/ensure', json={'root': str(root), 'files': ['text.yaml']}).json()
        result = wait(client, created['job']['id']); map_id = created['map']['id']
        args = {'snapshotId': result['snapshotId']}
        endpoint = '/api/document-maps/' + map_id + '/query/'
        graph = client.post(endpoint + 'graph', json=args).json()
        value = next(node for node in graph['nodes'] if len(node.get('preview', '')) > 100)
        content = client.post(endpoint + 'content', json={**args, 'nodeId': value['id'], 'offset': 0, 'limit': 4000})
        assert content.status_code == 200, content.text
        assert content.json()['nextOffset'] == 4000
        assert client.get('/api/map-jobs/' + result['id'], params={'mapId': 'other'}).status_code == 403
        assert client.get('/api/document-capabilities').json()['ocr'] is False


def test_large_discovery_pages_are_complete_bounded_and_scope_bound(tmp_path):
    """700 длинных имён доступны страницами; смена фильтра не переиспользует cursor."""
    root = tmp_path / 'repo'; root.mkdir()
    names = [f'{index:04d}-' + 'данные-' * 15 + '.md' for index in range(700)]
    for name in names:
        (root / name).write_text('# Fixture', encoding='utf-8')
    app = create_app(tmp_path / 'state')
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        seen, cursor = [], None
        while True:
            response = client.post('/api/document-files', json={'root': str(root), 'cursor': cursor})
            assert response.status_code == 200, response.text
            result = response.json()
            assert len(json.dumps(result, ensure_ascii=False).encode()) <= 64000
            seen.extend(item['path'] for item in result['files'])
            cursor = result['nextCursor']
            if not cursor:
                break
            assert client.post('/api/document-files', json={'root': str(root), 'cursor': cursor, 'query': '0001'}).status_code == 409
        assert seen == sorted(names)
