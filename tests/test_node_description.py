"""Самостоятельное описание одного символа, безопасность и точность ограничений."""
from copy import deepcopy
import json
import pytest
from atlas.node_description import build_node_description
from test_control_flow import branch_project


def test_node_description_is_single_source_and_preserves_graph(branch_project, monkeypatch):
    """Не обходит callee исходники; факты snapshot и документация рабочей копии различимы."""
    from atlas import execution_path_source
    project, graph = branch_project
    graph['nodes'].append({'id': 'outside', 'label': 'Missing', 'file': '../secret.cs'})
    graph['edges'].append({'source': 'run', 'target': 'outside', 'relation': 'calls'})
    before = deepcopy(graph)
    original = execution_path_source.read_node_source
    read = []
    def record(root, node, token, **kwargs):
        """Доказывает, что чтение соседних узлов не запускается."""
        read.append(node['id'])
        return original(root, node, token, **kwargs)
    monkeypatch.setattr(execution_path_source, 'read_node_source', record)
    result = build_node_description(project, graph, 'run', 's')
    assert read == ['run']
    assert graph == before
    assert result['nodeId'] == 'run' and result['snapshotId'] == 's'
    assert result['documentation']['summary'] == 'Processes <items> safely.'
    assert result['comment'] == 'Processes <items> safely.'
    assert result['commentSource']['sourceKind'] == 'working-tree'
    assert result['commentSource']['sourceHash'] == result['sourceHash']
    assert result['counts']['outgoing'] == 1
    assert result['factualSummary'] and result['sourceHash'] and result['analyzerVersion']


def test_node_description_totals_survive_edge_cap(branch_project):
    """Количество связей не подменяется размером ограниченного массива ответа."""
    project, graph = branch_project
    for index in range(205):
        node_id = str(index)
        graph['nodes'].append({'id': node_id, 'label': node_id, 'kind': 'external'})
        graph['edges'].append({'source': 'run', 'target': node_id, 'relation': 'calls'})
    result = build_node_description(project, graph, 'run', 's')
    assert result['counts']['outgoing'] == 205
    assert len(result['calls']['outgoing']) == 200
    assert result['truncation']['outgoing'] is True


def test_description_external_symlink_is_not_read(branch_project, tmp_path):
    """Общие безопасные чтения защищают и новый endpoint описания."""
    project, graph = branch_project
    outside = tmp_path / 'secret.cs'
    outside.write_text('/// <summary>OUTSIDE_SECRET</summary>\nclass Handler {void Run(){}}')
    (project / 'link.cs').symlink_to(outside)
    graph['nodes'][0]['file'] = 'link.cs'
    result = build_node_description(project, graph, 'run', 's')
    assert result['sourceStatus'] == 'unavailable'
    assert 'OUTSIDE_SECRET' not in json.dumps(result)


def test_description_api_snapshot_validation_and_race(branch_project, tmp_path, monkeypatch):
    """Новый endpoint проверяет токен до и после чтения и не принимает путь клиента."""
    from fastapi.testclient import TestClient
    from atlas import server, store
    from test_grouping_cache import legacy_store
    project, graph = branch_project
    state, project_id, snapshot, _ = legacy_store(tmp_path)
    store.write_json(snapshot, graph)
    state.projects[project_id]['path'] = str(project)
    store.write_json(state.registry, state.projects)
    state.close()
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        route = f'/api/projects/{project_id}/node-description'
        body = {'nodeId': 'run', 'snapshotId': snapshot.parent.name}
        assert client.post(route, json=body).status_code == 200
        assert client.post(route, json={'nodeId': 'run'}).status_code == 422
        assert client.post(route, json={**body, 'path': '/etc/passwd'}).status_code == 422
        assert client.post(route, json={**body, 'nodeId': 'entry:synthetic'}).status_code == 404
        original = server.build_node_description
        def forbidden(*args, **kwargs):
            """Отклонённый snapshot не читает файлы."""
            raise AssertionError('stale token read source')
        monkeypatch.setattr(server, 'build_node_description', forbidden)
        assert client.post(route, json={**body, 'snapshotId': 'stale'}).status_code == 409
        def publish(*args, **kwargs):
            """Подмена текущего снимка после чтения делает ответ непригодным для старой карты."""
            result = original(*args, **kwargs)
            newer = snapshot.parent.parent / 'newer' / 'atlas-graph.json'
            store.write_json(newer, graph)
            with client.app.state.store.lock:
                client.app.state.store.projects[project_id]['_graph'] = str(newer)
            return result
        monkeypatch.setattr(server, 'build_node_description', publish)
        assert client.post(route, json=body).status_code == 409
