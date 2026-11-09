"""Системная карта сохраняет доказательства проектов и редактирует секреты конфигурации."""
from copy import deepcopy
import json
import pytest


@pytest.fixture
def system_project(tmp_path):
    """Два локальных проекта с общим endpoint и намеренно чувствительной конфигурацией."""
    project = tmp_path / 'repository'
    (project / 'Api').mkdir(parents=True)
    (project / 'Worker').mkdir()
    (project / 'Api/Api.csproj').write_text('''<Project><ItemGroup>
<PackageReference Include="Example.Client" Version="1.2.3" />
<ProjectReference Include="../Worker/Worker.csproj" />
</ItemGroup></Project>''')
    (project / 'Worker/Worker.csproj').write_text('<Project/>')
    config = {
        'Service': {'BaseUrl': 'https://url-user:URL_PASSWORD@service.example:8443/api?token=QUERY_TOKEN#SECRET_FRAGMENT'},
        'ConnectionStrings': {'Main': 'Host=db.example;Port=5432;Database=portfolio;Username=DB_USER;Password=DB_PASSWORD'},
        'Kafka': {'BootstrapServers': 'broker.example:9092', 'SaslPassword': 'KAFKA_PASSWORD'},
        'RabbitMq': {'Host': 'rabbit.example', 'Username': 'RABBIT_USER', 'Password': 'RABBIT_PASSWORD'},
        'ApiToken': 'UNRELATED_TOKEN',
    }
    (project / 'Api/appsettings.json').write_text(json.dumps(config))
    (project / 'Worker/appsettings.json').write_text(json.dumps({'Service': {'BaseUrl': 'https://service.example:8443/api?secret=OTHER_TOKEN'}}))
    graph = {'nodes': [
        {'id': 'manifest-api', 'label': 'Api.csproj', 'file': 'Api/Api.csproj'},
        {'id': 'manifest-worker', 'label': 'Worker.csproj', 'file': 'Worker/Worker.csproj'},
        {'id': 'method-api', 'label': 'Run', 'file': 'Api/Run.cs', 'kind': 'method'},
        {'id': 'method-worker', 'label': 'Work', 'file': 'Worker/Work.cs', 'kind': 'method'},
    ], 'edges': [
        {'source': 'method-api', 'target': 'method-worker', 'relation': 'calls'},
        {'source': 'method-api', 'target': 'method-api', 'relation': 'calls'},
    ], 'metadata': {}}
    return project, graph


def test_system_graph_redacts_secrets_and_keeps_raw_immutable(system_project):
    """Ответ не содержит credentials/query и не подменяет исходный граф методом внешнего узла."""
    from atlas.system_architecture import build_system_architecture
    project, graph = system_project
    before = deepcopy(graph)
    result = build_system_architecture(project, graph, 's')
    encoded = json.dumps(result)
    for secret in ['URL_PASSWORD', 'url-user', 'QUERY_TOKEN', 'SECRET_FRAGMENT', 'DB_USER', 'DB_PASSWORD', 'KAFKA_PASSWORD', 'RABBIT_USER', 'RABBIT_PASSWORD', 'UNRELATED_TOKEN', 'OTHER_TOKEN']:
        assert secret not in encoded
    assert graph == before
    assert result['snapshotId'] == 's'
    assert not {'method-api', 'method-worker'} & {node['id'] for node in result['nodes']}
    assert all(edge['source'] != edge['target'] for edge in result['edges'])
    assert result['warning']


def test_system_shared_endpoint_is_one_node_with_owner_evidence(system_project):
    """Два владельца одного endpoint имеют общий объект и отдельные направленные доказательства."""
    from atlas.system_architecture import build_system_architecture
    project, graph = system_project
    result = build_system_architecture(project, graph, 's')
    endpoints = [node for node in result['nodes'] if node['kind'] == 'http']
    assert len(endpoints) == 1
    edges = [edge for edge in result['edges'] if edge['target'] == endpoints[0]['id']]
    assert len({edge['source'] for edge in edges}) == 2
    evidence = [item for edge in edges for item in edge['evidence']]
    assert {item['file'] for item in evidence} >= {'Api/appsettings.json', 'Worker/appsettings.json'}


def test_system_manifest_references_are_directed_and_packages_are_distinct(system_project):
    """PackageReference — зависимость сборки, ProjectReference направлен от владельца к цели."""
    from atlas.system_architecture import build_system_architecture
    project, graph = system_project
    result = build_system_architecture(project, graph, 's')
    projects = {node['label']: node for node in result['nodes'] if node['kind'] == 'project'}
    assert any(edge['source'] == projects['Api']['id'] and edge['target'] == projects['Worker']['id'] for edge in result['edges'])
    packages = [node for node in result['nodes'] if node['kind'] == 'package']
    assert any('Example.Client' in node['label'] for node in packages)
    assert not any('Example.Client' in node['label'] for node in result['nodes'] if node['kind'] in {'http', 'database', 'kafka', 'rabbit'})


def test_system_scan_never_connects_network(system_project, monkeypatch):
    """Конфигурация только читается: адреса не проверяются сетевыми запросами."""
    import socket
    from atlas.system_architecture import build_system_architecture
    def forbidden(*args, **kwargs):
        """Любая попытка сетевого соединения нарушает offline-контракт."""
        raise AssertionError('system architecture attempted network access')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    project, graph = system_project
    assert build_system_architecture(project, graph, 's')['nodes']


def test_system_scan_rejects_external_config_symlink(system_project, tmp_path):
    """Файл с допустимым именем не позволяет прочитать конфигурацию вне корня."""
    from atlas.system_architecture import build_system_architecture
    project, graph = system_project
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps({'Service': {'BaseUrl': 'https://outside-secret.example/'}}))
    (project / 'Api/appsettings.Development.json').symlink_to(outside)
    graph['nodes'].append({'id': 'escape', 'file': '../outside.json', 'label': 'config'})
    result = build_system_architecture(project, graph, 's')
    assert 'outside-secret' not in json.dumps(result)


def test_repeated_url_keys_keep_their_own_evidence_line():
    """Одинаковые BaseUrl в разных секциях не ссылаются все на первое совпадение."""
    from atlas.system_configuration import configured_systems
    text = '{\n  "First": {"BaseUrl": "https://first.example"},\n  "Second": {"BaseUrl": "https://second.example"}\n}'
    result = configured_systems(json.loads(text), text)
    by_host = {item['host']: item for item in result if item['kind'] == 'http'}
    assert by_host['first.example']['line'] == 2
    assert by_host['second.example']['line'] == 3


def test_kafka_nested_setting_is_not_double_counted():
    """Специальный и общий распознаватели не удваивают один broker declaration."""
    from atlas.system_configuration import configured_systems
    config = {'KafkaConsumersSettings': {'BrokerConnectionSettings': {'Main': {'BootstrapServers': 'broker.example:9092'}}}}
    result = configured_systems(config, json.dumps(config))
    brokers = [item for item in result if item['kind'] == 'kafka']
    assert len(brokers) == 1
    assert brokers[0]['host'] == 'broker.example:9092'


def test_system_environment_variants_remain_evidence(system_project):
    """Dev/prod настройки не считаются одновременно активными и имеют отдельное происхождение."""
    from atlas.system_architecture import build_system_architecture
    project, graph = system_project
    (project / 'Api/appsettings.Development.json').write_text(json.dumps({'Service': {'BaseUrl': 'https://dev.example'}}))
    result = build_system_architecture(project, graph, 's')
    variants = {item['configVariant'] for edge in result['edges'] for item in edge['evidence'] if item['configVariant']}
    assert {'appsettings.json', 'appsettings.Development.json'} <= variants
    assert 'активное окружение неизвестно' in result['warning']


def test_system_ambiguous_manifest_ownership_is_not_invented(system_project):
    """Два csproj в одном каталоге не дают уверенно присвоить общий config одному из них."""
    from atlas.system_architecture import build_system_architecture
    project, graph = system_project
    (project / 'Api/Other.csproj').write_text('<Project/>')
    graph['nodes'].append({'id': 'other-manifest', 'label': 'Other.csproj', 'file': 'Api/Other.csproj'})
    result = build_system_architecture(project, graph, 's')
    assert not any(item['file'] == 'Api/appsettings.json' for edge in result['edges'] for item in edge['evidence'])
    assert result['coverage'].get('ambiguousFiles', 0) > 0


def test_system_api_snapshot_race_and_validation(system_project, tmp_path, monkeypatch):
    """Системный endpoint проверяет snapshot до сканирования и после его завершения."""
    from fastapi.testclient import TestClient
    from atlas import server, store
    from test_grouping_cache import legacy_store
    project, graph = system_project
    state, project_id, snapshot, _ = legacy_store(tmp_path)
    store.write_json(snapshot, graph)
    state.projects[project_id]['path'] = str(project)
    store.write_json(state.registry, state.projects)
    state.close()
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        route = f'/api/projects/{project_id}/system-architecture'
        params = {'snapshotId': snapshot.parent.name}
        response = client.get(route, params=params)
        assert response.status_code == 200, response.text
        synthetic = next(node['id'] for node in response.json()['nodes'] if node['kind'] == 'http')
        for action in ['source', 'describe', 'execution-path', 'node-description']:
            assert client.post(f'/api/projects/{project_id}/{action}', json={'nodeId': synthetic, **params}).status_code == 404
        assert client.get(route).status_code == 422
        original = server.build_system_architecture
        def forbidden(*args, **kwargs):
            """Устаревшая карта не запускает scanner конфигурации."""
            raise AssertionError('stale system scan')
        monkeypatch.setattr(server, 'build_system_architecture', forbidden)
        assert client.get(route, params={'snapshotId': 'stale'}).status_code == 409
        def publish(*args, **kwargs):
            """Новая карта во время чтения делает системный ответ устаревшим."""
            result = original(*args, **kwargs)
            newer = snapshot.parent.parent / 'newer' / 'atlas-graph.json'
            store.write_json(newer, graph)
            with client.app.state.store.lock:
                client.app.state.store.projects[project_id]['_graph'] = str(newer)
            return result
        monkeypatch.setattr(server, 'build_system_architecture', publish)
        assert client.get(route, params=params).status_code == 409


def test_system_discovery_cap_is_visible(system_project):
    """Ограниченный просмотр каталога не выдаётся за полный индекс конфигурации."""
    from atlas.system_architecture import build_system_architecture
    project, graph = system_project
    for index in range(505):
        (project / 'Api' / f'unrelated-{index:04}.txt').touch()
    result = build_system_architecture(project, graph, 's')
    assert result['coverage']['limited'] is True


def test_system_file_budget_is_visible(system_project, monkeypatch):
    """Лимит чтения общий для файлов, а не сбрасывается для каждого проекта."""
    from atlas import system_architecture
    project, graph = system_project
    monkeypatch.setattr(system_architecture, 'MAX_FILES', 1)
    result = system_architecture.build_system_architecture(project, graph, 's')
    assert result['coverage']['readFiles'] <= 1
    assert result['coverage']['limited'] is True
    assert result['coverage']['skippedFiles'] > 0


def test_unknown_hosts_do_not_imply_shared_system(system_project):
    """Одинаковое логическое имя без известного endpoint не доказывает общую БД."""
    from atlas.system_architecture import build_system_architecture
    project, graph = system_project
    for owner in ['Api', 'Worker']:
        (project / owner / 'appsettings.json').write_text(json.dumps({'ConnectionStrings': {'Main': '${DB_CONNECTION}'}}))
    result = build_system_architecture(project, graph, 's')
    databases = [node for node in result['nodes'] if node['kind'] == 'database']
    assert len(databases) == 2
    assert all(node.get('host') is None for node in databases)


def test_broker_server_list_is_one_logical_dependency():
    """Bootstrap адреса одного подключения не превращаются в несколько независимых систем."""
    from atlas.system_configuration import configured_systems
    config = {'Kafka': {'BootstrapServers': 'b.example:9092,a.example:9092,b.example:9092'}}
    result = configured_systems(config, json.dumps(config))
    assert len(result) == 1
    assert result[0]['hosts'] == ['a.example:9092', 'b.example:9092']


def test_prepared_architecture_scope_does_not_cache_working_tree_content(system_project):
    """Повторная сборка с тем же ownership-контекстом читает свежую локальную конфигурацию."""
    from atlas.project_scope_context import ProjectScopeContext
    from atlas.system_architecture import build_system_architecture
    project,graph=system_project
    prepared=ProjectScopeContext(project,graph)
    before=build_system_architecture(project,graph,'snapshot',prepared=prepared)
    assert any(node.get('host')=='service.example:8443' for node in before['nodes'])
    for owner in ['Api','Worker']:
        (project/owner/'appsettings.json').write_text(json.dumps({'Service':{'BaseUrl':'https://changed.example'}}))
    after=build_system_architecture(project,graph,'snapshot',prepared=prepared)
    assert any(node.get('host')=='changed.example' for node in after['nodes'])
    assert not any(node.get('host')=='service.example:8443' for node in after['nodes'])
