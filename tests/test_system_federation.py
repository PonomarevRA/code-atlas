"""Общая системная карта сохраняет идентичность репозиториев и доказательства связей."""
from copy import deepcopy
import json
import pytest


@pytest.fixture
def repository_snapshots(tmp_path):
    """Одинаковые имена csproj/raw IDs, общие удалённые адреса и разные localhost."""
    snapshots = []
    for repo in ['one', 'two']:
        path = tmp_path / repo
        path.mkdir()
        reference = '<ProjectReference Include="../two/Service.csproj"/>' if repo == 'one' else ''
        (path / 'Service.csproj').write_text(f'<Project><ItemGroup>{reference}<PackageReference Include="Shared.Client" Version="1"/></ItemGroup></Project>')
        config = {f'Remote-{repo}': {'BaseUrl': 'https://api.example:443/path?token=SECRET'},
                  f'Local-{repo}': {'BaseUrl': 'http://localhost:9000/'}}
        (path / 'appsettings.json').write_text(json.dumps(config))
        graph = {'nodes': [{'id': 'manifest', 'file': 'Service.csproj', 'label': 'Service'}, {'id': 'run', 'file': 'Service.cs', 'label': 'Run', 'kind': 'method'}], 'edges': [], 'metadata': {}}
        snapshots.append({'id': repo, 'name': f'Repository {repo}', 'path': path, 'graph': graph, 'snapshotId': f's-{repo}'})
    return snapshots


def build(snapshots):
    """Вызывает публичную сборку федерации с закреплённой primary-ревизией."""
    from atlas.system_federation import build_federated_architecture
    return build_federated_architecture(snapshots, snapshots[0]['snapshotId'])


def test_same_project_names_are_namespaced_without_raw_mutation(repository_snapshots):
    """Коллизия manifest ID между репозиториями не объединяет реальные проекты."""
    before = deepcopy(repository_snapshots)
    result = build(repository_snapshots)
    projects = [node for node in result['nodes'] if node['kind'] == 'project']
    assert len(projects) == 2
    assert len({node['id'] for node in projects}) == 2
    assert {node['repositoryId'] for node in projects} == {'one', 'two'}
    assert len({node['originalCodeProjectId'] for node in projects}) == 1
    assert repository_snapshots == before
    assert {repo['snapshotId'] for repo in result['repositories']} == {'s-one', 's-two'}
    assert 'SECRET' not in json.dumps(result)


def test_remote_endpoints_merge_but_loopback_does_not(repository_snapshots):
    """Известный remote endpoint объединяется через aliases; localhost остаётся по владельцам."""
    result = build(repository_snapshots)
    remote = [node for node in result['nodes'] if node['kind'] == 'http' and 'api.example' in (node.get('host') or '')]
    local = [node for node in result['nodes'] if node['kind'] == 'http' and 'localhost' in (node.get('host') or '')]
    assert len(remote) == 1 and len(local) == 2
    assert set(remote[0]['repositoryIds']) == {'one', 'two'}
    evidence = [item for edge in result['edges'] if edge['target'] == remote[0]['id'] for item in edge['evidence']]
    assert {item['repositoryId'] for item in evidence} == {'one', 'two'}
    assert len([node for node in result['nodes'] if node['kind'] == 'package']) == 1


def test_cross_repository_reference_requires_exact_manifest_path(repository_snapshots):
    """Ссылка на соседний выбранный manifest разрешается; совпадения названия недостаточно."""
    result = build(repository_snapshots)
    projects = {node['repositoryId']: node['id'] for node in result['nodes'] if node['kind'] == 'project'}
    assert any(edge['source'] == projects['one'] and edge['target'] == projects['two'] and edge['relation'] == 'project-reference' for edge in result['edges'])
    path = repository_snapshots[0]['path'] / 'Service.csproj'
    path.write_text(path.read_text().replace('../two/Service.csproj', '../not-selected/Service.csproj'))
    result = build(repository_snapshots)
    assert not any(edge['source'] == projects['one'] and edge['target'] == projects['two'] for edge in result['edges'])
    assert any(node['kind'] == 'external-project' for node in result['nodes'])


@pytest.mark.parametrize('host', ['127.0.0.1', '[::1]', '0.0.0.0', 'localhost.localdomain'])
def test_loopback_variants_stay_repository_scoped(repository_snapshots, host):
    """IP-loopback и unspecified адрес не считаются общей удалённой системой."""
    for snapshot in repository_snapshots:
        (snapshot['path'] / 'appsettings.json').write_text(json.dumps({'Service': {'BaseUrl': f'http://{host}:9000/'}}))
    result = build(repository_snapshots)
    systems = [node for node in result['nodes'] if node['kind'] == 'http']
    assert len(systems) == 2
    assert {node['repositoryId'] for node in systems} == {'one', 'two'}


def test_federation_reordering_and_coverage_are_stable(repository_snapshots):
    """Порядок выбора не меняет общий граф; покрытие сохраняется по репозиториям."""
    first = build(repository_snapshots)
    from atlas.system_federation import build_federated_architecture
    second = build_federated_architecture(list(reversed(repository_snapshots)), 's-one')
    assert first == second
    assert {item['repositoryId'] for item in first['coverage']['perRepository']} == {'one', 'two'}


def test_federation_duplicate_path_does_not_guess_project(repository_snapshots):
    """Два выбранных snapshot одного manifest делают cross-reference неоднозначным."""
    duplicate = {**repository_snapshots[1], 'id': 'clone', 'name': 'Clone'}
    result = build([*repository_snapshots, duplicate])
    external = [node for node in result['nodes'] if node['kind'] == 'external-project']
    assert external


def test_federation_api_validation_and_secondary_snapshot_race(repository_snapshots, tmp_path, monkeypatch):
    """Проверяет выбранные карты и отклоняет смену даже вторичного снимка после build."""
    from atlas import store, server
    from fastapi.testclient import TestClient
    state = store.ProjectStore(tmp_path / 'state')
    paths = {}
    for snapshot in repository_snapshots:
        graph_path = state.data_dir / snapshot['id'] / snapshot['snapshotId'] / 'atlas-graph.json'
        store.write_json(graph_path, snapshot['graph'])
        paths[snapshot['id']] = graph_path
        state.projects[snapshot['id']] = {'id': snapshot['id'], 'name': snapshot['name'], 'path': str(snapshot['path']), 'status': 'completed', '_graph': str(graph_path)}
    state.projects['unbuilt'] = {'id': 'unbuilt', 'name': 'Unbuilt', 'path': str(tmp_path), 'status': 'failed'}
    store.write_json(state.registry, state.projects)
    state.close()
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        route = '/api/projects/one/system-architecture'
        body = {'snapshotId': 's-one', 'relatedProjectIds': ['two']}
        response = client.post(route, json=body)
        assert response.status_code == 200, response.text
        assert len(response.json()['repositories']) == 2
        for ids, status in [(['two','two'],400), (['one'],400), (['absent'],404), (['unbuilt'],409), (list(map(str,range(9))),422)]:
            assert client.post(route, json={**body,'relatedProjectIds':ids}).status_code == status
        assert client.post(route,json={'relatedProjectIds':['two']}).status_code == 422
        assert client.post(route,json={**body,'path':'/etc/passwd'}).status_code == 422
        original = server.build_federated_architecture
        def forbidden(*args, **kwargs):
            """Невалидный primary snapshot не запускает общий scanner."""
            raise AssertionError('stale federation scanned source')
        monkeypatch.setattr(server,'build_federated_architecture',forbidden)
        assert client.post(route,json={**body,'snapshotId':'stale'}).status_code == 409
        def publish_secondary(*args, **kwargs):
            """Меняет только secondary карту во время build, primary остаётся прежней."""
            result = original(*args, **kwargs)
            changed = paths['two'].parent.parent / 'new-snapshot' / 'atlas-graph.json'
            store.write_json(changed, repository_snapshots[1]['graph'])
            with client.app.state.store.lock:
                client.app.state.store.projects['two']['_graph'] = str(changed)
            return result
        monkeypatch.setattr(server,'build_federated_architecture',publish_secondary)
        assert client.post(route,json=body).status_code == 409
