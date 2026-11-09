"""Доказуемые локальные входы отделены от графа и не требуют запуска приложения."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from atlas.entrypoints import build_entrypoints


@pytest.fixture
def ingress_project(tmp_path):
    """Исходники с HTTP/Kafka/Rabbit входами и посторонним секретом конфигурации."""
    project = tmp_path / "project"
    project.mkdir()
    controller = '''[Route("api/[controller]")]
public class OrdersController : ControllerBase {
    [HttpGet("{id}")]
    public string Get(int id) { return "ok"; }
    [HttpPost]
    public void Save() {}
    // [HttpDelete("not-real")] public void Commented() {}
}
'''
    (project / "OrdersController.cs").write_text(controller)
    (project / "App.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk.Web"/>')
    graph = {"nodes": [
        {"id": "manifest", "file": "App.csproj", "label": "App", "line": 1},
        {"id": "controller", "file": "OrdersController.cs", "label": "OrdersController", "line": 2},
        {"id": "get", "file": "OrdersController.cs", "label": "Get", "line": 4},
        {"id": "save", "file": "OrdersController.cs", "label": "Save", "line": 6},
    ], "edges": [{"source": "controller", "target": "get", "relation": "contains"}], "metadata": {}}
    return project, graph


def test_http_routes_evidence_mapping_and_raw_preservation(ingress_project):
    """Входы имеют реальные маршруты и строки, а ID и факты Graphify не меняются."""
    project, graph = ingress_project
    before = deepcopy(graph)
    result = build_entrypoints(project, graph, "snapshot")
    assert graph == before
    assert result["snapshotId"] == "snapshot"
    assert result["sourceKind"] == "working-tree"
    assert result["warning"]
    http = [entry for entry in result["entries"] if entry["kind"] == "http"]
    assert len(http) == 2
    assert {entry["httpMethod"] for entry in http} == {"GET", "POST"}
    assert any("{id}" in entry["route"] for entry in http)
    for entry in http:
        assert entry["id"] not in {node["id"] for node in graph["nodes"]}
        assert entry["evidence"]
        assert all(item["file"] == "OrdersController.cs" and item["line"] > 0 for item in entry["evidence"])
        assert set(entry["handlerNodeIds"]) <= {node["id"] for node in graph["nodes"]}
        assert "not-real" not in json.dumps(entry)


def test_entrypoint_scan_does_not_follow_external_symlink(ingress_project, tmp_path):
    """Снимок с внешним .cs symlink не позволяет извлечь посторонние маршруты."""
    project, graph = ingress_project
    outside = tmp_path / "outside.cs"
    outside.write_text('[Route("external-secret")][HttpGet] public void Leak() {}')
    (project / "linked.cs").symlink_to(outside)
    graph["nodes"].append({"id": "linked", "label": "Leak", "file": "linked.cs", "line": 1})
    result = build_entrypoints(project, graph, "s")
    assert "external-secret" not in json.dumps(result)


def test_scan_refreshes_current_sources_without_stale_cache(ingress_project):
    """Повторный scan отражает рабочую копию при прежнем снимке Graphify."""
    project, graph = ingress_project
    first = build_entrypoints(project, graph, "s")
    source = project / "OrdersController.cs"
    source.write_text(source.read_text().replace('api/[controller]', 'changed/[controller]'))
    second = build_entrypoints(project, graph, "s")
    assert {entry["route"] for entry in first["entries"] if entry["kind"] == "http"} != {entry["route"] for entry in second["entries"] if entry["kind"] == "http"}


def test_entrypoint_http_snapshot_and_synthetic_guards(ingress_project, tmp_path, monkeypatch):
    """API не смешивает снимки и не принимает overlay ID за исходный символ/MCP-узел."""
    from fastapi.testclient import TestClient
    from atlas import store, server
    from test_grouping_cache import legacy_store
    project, graph = ingress_project
    state, project_id, snapshot, _ = legacy_store(tmp_path)
    store.write_json(snapshot, graph)
    state.projects[project_id]["path"] = str(project)
    store.write_json(state.registry, state.projects)
    state.close()
    with TestClient(server.create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        route = f"/api/projects/{project_id}/entrypoints"
        token = snapshot.parent.name
        response = client.get(route, params={"snapshotId": token})
        assert response.status_code == 200, response.text
        entry_id = response.json()["entries"][0]["id"]
        assert client.get(route, params={"snapshotId": "stale"}).status_code == 409
        assert client.get(route).status_code == 422
        assert client.post(f"/api/projects/{project_id}/source", json={"nodeId": entry_id, "snapshotId": token}).status_code == 404
        assert client.post(f"/api/projects/{project_id}/describe", json={"nodeId": entry_id, "snapshotId": token}).status_code == 404
        original = server.build_entrypoints
        def publish_new_snapshot(*args, **kwargs):
            """Имитирует завершение индексации внутри долгого scan текущих файлов."""
            result = original(*args, **kwargs)
            current = client.app.state.store
            newer = snapshot.parent.parent / "new-snapshot" / "atlas-graph.json"
            store.write_json(newer, graph)
            with current.lock:
                current.projects[project_id]["_graph"] = str(newer)
            return result
        monkeypatch.setattr(server, "build_entrypoints", publish_new_snapshot)
        assert client.get(route, params={"snapshotId": token}).status_code == 409


def test_http_route_attributes_do_not_bleed_into_next_class():
    """Контроллер без своего маршрута не наследует атрибут соседнего класса."""
    from atlas.entrypoint_parsers import controller_routes, parse_http
    text = '[Route("first")] class FirstController { [HttpGet] public void A() {} }\nclass SecondController { [HttpGet] public void B() {} }'
    entries = parse_http(text, controller_routes(text))
    assert next(entry for entry in entries if entry["handler"] == "A")["route"] == "/first"
    assert next(entry for entry in entries if entry["handler"] == "B")["route"] is None


def test_rabbit_handler_is_scoped_to_receive_endpoint():
    """Чужая регистрация после завершённой лямбды не становится обработчиком очереди."""
    from atlas.entrypoint_parsers import parse_messaging
    entries = parse_messaging('cfg.ReceiveEndpoint("q1", e => {}); cfg.ConfigureConsumer<Unrelated>();')
    assert len(entries) == 1
    assert entries[0]["queue"] == "q1"
    assert entries[0]["handler"] is None


def test_actual_messaging_literals_and_no_secret_or_producer_inference(ingress_project):
    """Kafka topic и Rabbit queue имеют явные регистрации, producer и секреты не выдаются."""
    project, graph = ingress_project
    text = '''services.AddPortfolioBatchConsumer<OrderMessage,OrdersHandler>(consumerSettings.Orders, options);
cfg.ReceiveEndpoint("incoming-orders", e => { e.ConfigureConsumer<OrdersHandler>(context); });
channel.BasicPublish("not-an-input", "private-routing-key", bytes);
// cfg.ReceiveEndpoint("commented-queue", e => e.ConfigureConsumer<Fake>());
'''
    (project / "Messaging.cs").write_text(text)
    (project / "OrdersHandler.cs").write_text('class OrdersHandler { public void Handle() {} }')
    graph["nodes"] += [{"id": "registration", "file": "Messaging.cs", "label": "Register", "line": 1}, {"id": "handler", "file": "OrdersHandler.cs", "label": "OrdersHandler", "line": 1}]
    (project / "appsettings.json").write_text(json.dumps({"KafkaConsumersSettings": {"Orders": {"TopicName": "orders.events", "Password": "NEVER_REVEAL_SECRET"}}, "ConnectionStrings": {"Private": "NEVER_REVEAL_CONNECTION"}}))
    result = build_entrypoints(project, graph, "s")
    kafka = [entry for entry in result["entries"] if entry["kind"] == "kafka"]
    rabbit = [entry for entry in result["entries"] if entry["kind"] == "rabbit"]
    assert len(kafka) == len(rabbit) == 1
    assert kafka[0]["topic"] == "orders.events"
    assert rabbit[0]["queue"] == "incoming-orders"
    assert "handler" in kafka[0]["handlerNodeIds"]
    payload = json.dumps(result)
    for secret in ["NEVER_REVEAL_SECRET", "NEVER_REVEAL_CONNECTION", "not-an-input", "private-routing-key", "commented-queue"]:
        assert secret not in payload
    (project / "appsettings.Development.json").write_text(json.dumps({"KafkaConsumersSettings": {"Orders": {"TopicName": "dev.orders"}}}))
    variants = build_entrypoints(project, graph, "s")
    assert {entry["topic"] for entry in variants["entries"] if entry["kind"] == "kafka"} == {"orders.events", "dev.orders"}


def test_computed_http_route_is_not_reported_as_literal():
    """Строковый префикс вычисляемого выражения не выдаётся за полный URL."""
    from atlas.entrypoint_parsers import controller_routes, parse_http
    text = '[Route("api")] class AController { [HttpGet("fixed" + variable)] public void Read() {} }'
    entries = parse_http(text, controller_routes(text))
    assert len(entries) == 1
    assert entries[0]["route"] is None


def test_code_inside_strings_is_not_an_entrypoint():
    """Примеры кода в строках не создают фиктивные обработчики и подписки."""
    from atlas.entrypoint_parsers import controller_routes, parse_http, parse_messaging
    text = '''[Route("api")] class AController {
string documentation = "[HttpGet] public void Fake() {}";
string registration = "services.AddPortfolioBatchConsumer<Message,FakeHandler>(consumerSettings.Fake, options)";
}'''
    assert parse_http(text, controller_routes(text)) == []
    assert parse_messaging(text) == []


def test_scan_coverage_is_explicit_and_network_not_used(ingress_project, monkeypatch):
    """Ограниченный scan помечает неполноту и не пытается подключаться к сервисам."""
    import socket
    from atlas import entrypoints
    project, graph = ingress_project
    (project / "Second.cs").write_text('class Empty {}')
    graph["nodes"].append({"id": "second", "file": "Second.cs", "label": "Empty"})
    def forbidden_connect(*args, **kwargs):
        """Любая попытка сетевого соединения делает тест неуспешным."""
        raise AssertionError("entrypoint scan must stay offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    monkeypatch.setattr(entrypoints, "MAX_FILES", 1)
    result = build_entrypoints(project, graph, "s")
    assert result["coverage"]["limited"] is True
    assert result["coverage"]["readFiles"] == 1
    assert result["coverage"]["skippedFiles"] >= 1


def test_ambiguous_handler_names_do_not_create_false_mapping(ingress_project):
    """Одинаковые имена в одном файле не дают доказанного соответствия обоим символам."""
    project, graph = ingress_project
    graph["nodes"].append({"id": "other-get", "file": "OrdersController.cs", "label": "Get", "line": 100})
    entries = build_entrypoints(project, graph, "s")["entries"]
    get = next(entry for entry in entries if entry["kind"] == "http" and entry["httpMethod"] == "GET")
    assert get["handlerNodeIds"] in (["get"], [])
    if not get["handlerNodeIds"]:
        assert get["confidence"] == "unresolved"
        assert get["unresolved"]


def test_method_route_attribute_is_not_silently_ignored():
    """Отдельный Route у метода учитывается либо честно остаётся неразрешённым."""
    from atlas.entrypoint_parsers import controller_routes, parse_http
    text = '[Route("api")] class CController { [Route("other")][HttpGet] public void Read() {} }'
    entries = parse_http(text, controller_routes(text))
    assert len(entries) == 1
    assert entries[0]["route"] in (None, "/api/other")


def test_prepared_source_keeps_parser_meaning():
    """Общий лексический проход сохраняет маршруты, смещения и потребителей."""
    from atlas.entrypoint_parsers import prepare_source, controller_routes, parse_http, parse_messaging
    text = '''// [HttpDelete("fake")]
[Route("api/[controller]")]
public class OrdersController {
 [HttpGet("{id}")] public string Get(int id) { return "[HttpPost] public void Fake() {}"; }
 public void Configure() {
   bus.ReceiveEndpoint("orders", e => e.ConfigureConsumer<OrdersConsumer>(context));
 }
}'''
    prepared = prepare_source(text)
    routes = controller_routes(prepared)
    assert routes == controller_routes(text) == {"OrdersController": "api/[controller]"}
    assert parse_http(prepared, routes) == parse_http(text, routes)
    assert len(parse_http(prepared, routes)) == 1
    assert parse_http(prepared, routes)[0]["line"] == 4
    assert parse_messaging(prepared) == parse_messaging(text)
    assert parse_messaging(prepared)[0]["queue"] == "orders"
