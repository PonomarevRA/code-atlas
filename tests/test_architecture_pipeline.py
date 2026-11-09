"""Настоящий Graphify → опубликованный снимок → HTTP-архитектура без исполнения проекта."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from atlas.server import create_app
from atlas.store import ProjectStore
from conftest import source_snapshot


def write_architecture_fixture(root: Path) -> None:
    """
    Создаёт исходники с намеренными секретами и запретом исполнения.

    Args:
        root: Новый временный каталог исходников; его родитель должен существовать.
    """
    root.mkdir(parents=True)
    (root / 'App.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>')
    (root / 'Flow.cs').write_text('''using Confluent.Kafka;
using RabbitMQ;
using Dapper;
using System.Net.Http;
class Flow {
 public void Produce(){ producer.ProduceAsync("orders", message); }
 public void Consume(){ channel.BasicConsume(queue:"orders.queue"); }
 public void Query(){ connection.Query("SELECT id FROM orders WHERE secret='SQL_SECRET'"); }
 public void Http(){ httpClient.GetAsync("https://user:HTTP_SECRET@api.example.test/v1/orders?token=QUERY_SECRET"); }
}''')
    (root / 'client.py').write_text('''import requests
raise RuntimeError("Atlas must never execute this project")
def external_call():
    requests.get("https://user:HTTP_SECRET@python.example.test/v1/orders?token=QUERY_SECRET")
''')
    (root / 'appsettings.json').write_text(json.dumps({
        'ConnectionStrings': {'One': 'Host=one.example;Database=first;Password=CONFIG_SECRET',
                              'Two': 'Host=two.example;Database=second;Password=OTHER_SECRET'},
        'Kafka': {'BootstrapServers': 'broker.example:9092'},
        'RabbitMq': {'Host': 'rabbit.example'},
        'Service': {'BaseUrl': 'https://api.example.test?token=CONFIG_SECRET'}}))


def build_published_fixture(root: Path, state_dir: Path) -> dict:
    """
    Публикует настоящий снимок Graphify, ожидая не больше90секунд.

    Args:
        root: Абсолютный путь к подготовленным синтетическим исходникам.
        state_dir: Отдельное от исходников хранилище этого тестового Atlas.
    Returns:
        ID проекта и снимка с настоящими rawID пяти проверяемых объявлений.
    Raises:
        AssertionError: Сборка не завершилась успешно или ожидаемые символы отсутствуют.
    """
    state = ProjectStore(state_dir)
    try:
        added = state.add(str(root))
        job_id = added['job']['id']
        deadline = time.monotonic() + 90
        while state.active_job and time.monotonic() < deadline:
            time.sleep(0.05)
        job = state.jobs[job_id]
        assert job['status'] == 'completed', job
        project_id = added['project']['id']
        _, graph = state.comparison_snapshot(project_id)
        visits = {}
        for node in graph['nodes']:
            label = node.get('label', '').lower()
            for name in ['produce', 'consume', 'query', 'http', 'external_call']:
                if label.strip('.()') == name and node.get('file') in {'Flow.cs', 'client.py'}:
                    visits[name] = node['id']
        assert len(visits) == 5, [(n.get('label'), n.get('kind')) for n in graph['nodes']]
        return {'projectId': project_id, 'snapshotId': graph['metadata']['snapshotId'], 'visits': visits}
    finally:
        state.close()


@pytest.fixture(scope='module')
def published_architecture(tmp_path_factory):
    """
    Передаёт HTTP-клиент, исходники и контекст одного настоящего снимка.

    Args:
        tmp_path_factory: Фабрика pytest для изолированных каталогов модуля.
    Yields:
        Клиент с управляемым lifespan, путь исходников, их байты и контекст снимка.
    """
    directory = tmp_path_factory.mktemp('architecture-pipeline')
    root, state_dir = directory / 'source', directory / 'state'
    write_architecture_fixture(root)
    before = source_snapshot(root)
    context = build_published_fixture(root, state_dir)
    with TestClient(create_app(state_dir), base_url='http://127.0.0.1:8765') as http:
        yield http, root, before, context
    assert source_snapshot(root) == before


def test_real_graphify_http_incremental_facts_and_raw_immutability(published_architecture):
    """
    Проверяет четыре типа ресурсов, стабильность ID и неизменность raw-графа.

    Args:
        published_architecture: Настоящий HTTP-клиент и опубликованный Graphify-контекст.
    """
    http, root, before, context = published_architecture
    route = f"/api/projects/{context['projectId']}/system-architecture"
    body = {'snapshotId': context['snapshotId'], 'relatedProjectIds': [], 'visitedNodeIds': []}
    state = http.app.state.store
    _, original = state.comparison_snapshot(context['projectId'])
    captured = deepcopy(original)
    response = http.post(route, json=body)
    assert response.status_code == 200, response.text
    baseline = response.json()
    result = http.post(route, json={**body, 'visitedNodeIds': list(context['visits'].values())})
    assert result.status_code == 200, result.text
    enriched = result.json()
    assert enriched['architectureVersion'] == 2
    assert {n['id'] for n in baseline['nodes']} <= {n['id'] for n in enriched['nodes']}
    assert {n['resourceKind'] for n in enriched['nodes'] if n.get('resourceKind')} == {'topic', 'queue', 'table', 'endpoint'}
    assert enriched['exploration']['addedEdges'] == 5
    again = http.post(route, json={**body, 'visitedNodeIds': list(reversed(list(context['visits'].values())))})
    assert again.json() == enriched
    encoded = json.dumps(enriched)
    for secret in ['SQL_SECRET', 'HTTP_SECRET', 'QUERY_SECRET', 'CONFIG_SECRET', 'OTHER_SECRET', '/v1/orders']:
        assert secret not in encoded
    _, after = state.comparison_snapshot(context['projectId'])
    assert after == captured
    assert source_snapshot(root) == before


def test_real_http_rejects_stale_unknown_and_overbudget_visits(published_architecture):
    """
    Проверяет отказ для устаревшего снимка, синтетического ID и65посещений.

    Args:
        published_architecture: Настоящий HTTP-клиент и опубликованный Graphify-контекст.
    """
    http, _, _, context = published_architecture
    route = f"/api/projects/{context['projectId']}/system-architecture"
    body = {'snapshotId': context['snapshotId'], 'visitedNodeIds': []}
    assert http.post(route, json={**body, 'snapshotId': 'stale'}).status_code == 409
    assert http.post(route, json={**body, 'visitedNodeIds': ['system-not-raw']}).status_code == 404
    assert http.post(route, json={**body, 'visitedNodeIds': ['x'] * 65}).status_code == 422


def test_real_http_parallel_requests_keep_exact_facts(published_architecture):
    """
    Проверяет одинаковые факты в32запросах восьми параллельных клиентов.

    Args:
        published_architecture: Общий опубликованный снимок и HTTP-клиент.
    """
    http, _, _, context = published_architecture
    route = f"/api/projects/{context['projectId']}/system-architecture"
    body = {'snapshotId': context['snapshotId'], 'visitedNodeIds': list(context['visits'].values())}
    expected = http.post(route, json=body).json()
    def request(_):
        """
        Возвращает проверенный JSON одного настоящего HTTP-запроса.

        Args:
            _: Номер запроса ThreadPoolExecutor; не влияет на тело запроса.
        """
        response = http.post(route, json=body)
        assert response.status_code == 200, response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(32)))
    assert all(result == expected for result in results)


def test_real_graphify_resource_cap_is_reported_without_dangling_edges(tmp_path):
    """
    Проверяет ограничение250Kafka-вызовов240ресурсами без потерянных концов связей.

    Args:
        tmp_path: Отдельный каталог pytest для исходников и хранилища этого случая.
    """
    root = tmp_path / 'large-source'
    write_architecture_fixture(root)
    source = root / 'Flow.cs'
    calls = '\n'.join(f'producer.ProduceAsync("topic-{index:03}", message);' for index in range(250))
    # Этот сценарий использует только Kafka; остальные имена нужны helper для проверки публикации.
    declarations = '\n}\nvoid Consume(){}\nvoid Query(){}\nvoid Http(){}\n}'
    source.write_text('using Confluent.Kafka;\nclass Flow { public void Produce(){\n' + calls + declarations)
    before = source_snapshot(root)
    context = build_published_fixture(root, tmp_path / 'large-state')
    with TestClient(create_app(tmp_path / 'large-state'), base_url='http://127.0.0.1:8765') as http:
        response = http.post(f"/api/projects/{context['projectId']}/system-architecture", json={
            'snapshotId': context['snapshotId'], 'visitedNodeIds': [context['visits']['produce']]})
        assert response.status_code == 200, response.text
        result = response.json()
        resources = [node for node in result['nodes'] if node.get('resourceKind')]
        assert len(resources) == 240
        assert result['exploration']['limited'] is True
        assert result['exploration']['coverage']['complete'] is False
        ids = {node['id'] for node in result['nodes']}
        assert all(edge['source'] in ids and edge['target'] in ids for edge in result['edges'])
    assert source_snapshot(root) == before

