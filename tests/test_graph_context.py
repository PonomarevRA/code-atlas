"""Чтение захваченного графа: направление, атрибуты, UTF-8 бюджет и MCP project guard."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from atlas import agent_mcp
from atlas.graph_context import GraphContextError, MAX_RESPONSE_BYTES, read_graph_neighbors, read_graph_node


def graph_fixture():
    """Атрибут параметра и объявления — отдельные узлы; их связи не являются calls."""
    return {
        "metadata": {"snapshotId": "snapshot", "attributeGraph": {"extractorVersion": "v1", "status": "captured"}},
        "nodes": [
            {"id": "method", "label": "Calculate", "kind": "method", "file": "src/Calc.cs", "line": 10,
             "attributeNodeIds": ["attr", "parameter-attr"],
             "attributeFacts": {"status": "parsed", "groups": [{"scope": "declaration", "name": "Calculate", "attributes": [{"name": "HttpPost", "text": '[HttpPost("calculate")]'}]}], "truncated": False}},
            {"id": "caller", "label": "Caller", "kind": "method"},
            {"id": "attr", "label": "HttpPost", "kind": "attribute", "file": "src/Calc.cs", "line": 9,
             "ownerNodeId": "method", "declarationNodeId": "method", "scope": "declaration", "target": None, "text": '[HttpPost("calculate")]'},
            {"id": "parameter-attr", "label": "FromBody", "kind": "attribute", "ownerNodeId": "method", "declarationNodeId": "method", "scope": "parameter"},
        ],
        "edges": [
            {"source": "method", "target": "attr", "relation": "has_attribute", "confidence": "source"},
            {"source": "method", "target": "parameter-attr", "relation": "has_parameter_attribute"},
            {"source": "caller", "target": "method", "relation": "calls"},
            {"source": "method", "target": "method", "relation": "calls"},
            {"source": "method", "target": "attr", "relation": "documents"},
            {"source": "method", "target": "missing", "relation": "calls"},
        ],
    }


def byte_size(result):
    """Использует тот же UTF-8 encoding, а не число Unicode символов."""
    return len(json.dumps(result, ensure_ascii=False).encode("utf-8"))


def test_node_keeps_all_snapshot_facts_without_live_access(monkeypatch):
    """Вместившиеся факты копируются буквально и не зависят от файлов на диске."""
    graph = graph_fixture()
    original = deepcopy(graph)

    def deny(*args, **kwargs):
        """Любое чтение исходников нарушило бы snapshot-only контракт."""
        raise AssertionError("Unexpected file access")

    monkeypatch.setattr("builtins.open", deny)
    result = read_graph_node(graph, "method")
    assert result["node"] == graph["nodes"][0]
    assert result["attributeGraph"] == graph["metadata"]["attributeGraph"]
    assert result["snapshotId"] == "snapshot" and not result["truncated"]
    result["node"]["attributeFacts"]["groups"][0]["name"] = "Changed"
    result["attributeGraph"]["status"] = "Changed"
    assert graph == original


def test_single_node_keeps_last_duplicate_identity_without_copying_other_nodes():
    """Published lookup preserves the old dictionary's last-value rule and ignores unrelated heavy facts."""
    graph = graph_fixture()
    graph['nodes'].append({'id': 'method', 'label': 'Latest captured declaration', 'kind': 'method'})
    result = read_graph_node(graph, 'method')
    assert result['node']['label'] == 'Latest captured declaration'
    assert 'attributeFacts' not in result['node']


@pytest.mark.parametrize(("direction", "total"), [("incoming", 2), ("outgoing", 4), ("both", 5)])
def test_neighbor_direction_self_loop_and_duplicate_relations(direction, total):
    """Самопетля одна, различные связи к одному узлу остаются различными фактами."""
    graph = graph_fixture()
    original = deepcopy(graph)
    result = read_graph_neighbors(graph, "method", direction)
    assert result["total"] == total == len(result["edges"])
    assert result["snapshotId"] == "snapshot" and not result["truncated"]
    assert sum(edge["source"] == edge["target"] for edge in result["edges"]) == 1
    endpoints = {endpoint for edge in result["edges"] for endpoint in (edge["source"], edge["target"])}
    assert {node["id"] for node in result["nodes"]} == endpoints | {"method"}
    assert all("attributeFacts" not in node for node in result["nodes"])
    assert graph == original


def test_attribute_scope_and_ownership_survive_neighbor_compaction():
    """Параметр, владелец и target доступны для последующего чтения get_node."""
    result = read_graph_neighbors(graph_fixture(), "method", "outgoing", 2)
    assert result["total"] == 4 and result["truncated"]
    assert [edge["relation"] for edge in result["edges"]] == ["has_attribute", "has_parameter_attribute"]
    attribute = next(node for node in result["nodes"] if node["id"] == "attr")
    assert attribute["ownerNodeId"] == attribute["declarationNodeId"] == "method"
    assert attribute["scope"] == "declaration" and attribute["target"] is None
    assert attribute["text"] == '[HttpPost("calculate")]'
    assert next(node for node in result["nodes"] if node["id"] == "parameter-attr")["scope"] == "parameter"


@pytest.mark.parametrize("direction", ["sideways", "", None, []])
def test_bad_direction_is_controlled(direction):
    """Неправильное направление — ошибка запроса до выборки графа."""
    with pytest.raises(GraphContextError) as error:
        read_graph_neighbors(graph_fixture(), "method", direction)
    assert error.value.status == 400


@pytest.mark.parametrize("limit", [0, 121, True, 1.5, "40", None])
def test_bad_limit_is_controlled(limit):
    """Не допускает неявной конвертации bool/float/string в лимит рёбер."""
    with pytest.raises(GraphContextError) as error:
        read_graph_neighbors(graph_fixture(), "method", limit=limit)
    assert error.value.status == 400


@pytest.mark.parametrize("reader", [read_graph_node, read_graph_neighbors])
def test_unknown_node_is_404(reader):
    """Отсутствие raw-ID не маскируется пустой успешной выборкой."""
    with pytest.raises(GraphContextError) as error:
        reader(graph_fixture(), "not-in-snapshot")
    assert error.value.status == 404


def test_large_unicode_attribute_facts_trim_preserving_scopes_and_ids():
    """Большие группы сокращаются честно; ссылки на атрибуты и владельцев остаются пригодными."""
    graph = graph_fixture()
    graph["nodes"][0]["attributeFacts"]["groups"] = [
        {"scope": scope, "name": scope, "file": "src/Calc.cs", "line": 10,
         "attributes": [{"name": f"Атрибут{i}", "text": "Ж" * 5000, "arguments": [{"expression": "😀" * 1000}]} for i in range(80)]}
        for scope in ("declaration", "type", "parameter", "file")
    ]
    original = deepcopy(graph)
    result = read_graph_node(graph, "method")
    assert byte_size(result) <= MAX_RESPONSE_BYTES
    assert result["truncated"] and result["node"]["truncated"]
    facts = result["node"]["attributeFacts"]
    assert facts["truncated"]
    assert [group["scope"] for group in facts["groups"]] == ["declaration", "type", "parameter", "file"]
    assert any(group.get("truncated") for group in facts["groups"])
    assert result["node"]["attributeNodeIds"] == ["attr", "parameter-attr"]
    assert graph == original


def test_neighbor_byte_budget_preserves_endpoint_integrity_and_source_evidence():
    """При большом UTF-8 ответе не остаются рёбра без возвращённых концов."""
    graph = graph_fixture()
    graph["edges"] = []
    for index in range(120):
        identifier = f"attr:{index}"
        graph["nodes"].append({"id": identifier, "label": "Атрибут" * 500, "kind": "attribute", "text": "😀" * 1000, "scope": "parameter", "ownerNodeId": "method"})
        graph["edges"].append({"source": "method", "target": identifier, "relation": "has_parameter_attribute", "evidence": {"file": "src/Calc.cs", "line": index + 1}})
    original = deepcopy(graph)
    result = read_graph_neighbors(graph, "method", limit=120)
    assert byte_size(result) <= MAX_RESPONSE_BYTES
    assert result["truncated"] and result["total"] == 120
    assert 0 < len(result["edges"]) < 120
    returned = {node["id"] for node in result["nodes"]}
    assert all(edge["source"] in returned and edge["target"] in returned for edge in result["edges"])
    assert result["edges"][0]["evidence"] == {"file": "src/Calc.cs", "line": 1}
    assert graph == original


def test_empty_neighbors_keep_selected_node():
    """Узел без связей остаётся адресуемым и возвращает total=0."""
    graph = graph_fixture()
    graph["edges"] = []
    result = read_graph_neighbors(graph, "attr")
    assert result["total"] == 0 and result["edges"] == []
    assert [node["id"] for node in result["nodes"]] == ["attr"]


def test_large_node_text_and_nested_collections_are_explicitly_bounded():
    """Усечение действует и вне attributeFacts, сохраняя имена и пригодные ID."""
    graph = graph_fixture()
    graph["nodes"][0]["text"] = "😀" * 100_000
    graph["nodes"][0]["attributeNodeIds"] = [f"attribute:{i}" for i in range(400)]
    graph["nodes"][0]["extra"] = {str(i): "Ж" * 4000 for i in range(300)}
    result = read_graph_node(graph, "method")
    assert byte_size(result) <= MAX_RESPONSE_BYTES
    assert result["node"]["id"] == "method"
    assert result["node"]["attributeNodeIdsTruncated"]
    assert all(identifier in graph["nodes"][0]["attributeNodeIds"] for identifier in result["node"]["attributeNodeIds"])
    assert result["node"]["attributeFacts"]["truncated"] and result["truncated"]


@pytest.mark.parametrize("reader", [read_graph_node, read_graph_neighbors])
def test_impossibly_large_identity_fails_without_slicing_raw_id(reader):
    """Невмещаемый raw-ID нельзя заменить несуществующим обрезанным идентификатором."""
    identifier = "узел" * 20_000
    graph = {"metadata": {"snapshotId": "snapshot"}, "nodes": [{"id": identifier}], "edges": []}
    with pytest.raises(GraphContextError) as error:
        reader(graph, identifier)
    assert error.value.status == 413


def test_mcp_graph_tools_use_selected_project_and_snapshot_before_http(monkeypatch):
    """Реальные SDK-инструменты проксируют поля и отклоняют другой project до HTTP."""
    requests = []

    def transport(request):
        """Запоминает сетевой контракт loopback API без запуска HTTP сервера."""
        requests.append(request)
        return httpx.Response(200, json={"snapshotId": "s", "node": {"id": "method"}})

    original = httpx.AsyncClient

    def client(**options):
        """Проверяет отключение env-proxy и redirects в существующем MCP boundary."""
        assert not options["trust_env"] and not options["follow_redirects"]
        return original(**options, transport=httpx.MockTransport(transport))

    monkeypatch.setattr(agent_mcp.httpx, "AsyncClient", client)

    async def scenario():
        """Вызывает зарегистрированные SDK tools и их project guard."""
        mcp = agent_mcp.create_mcp(8877, project_id="selected")
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        assert tools["get_node"].annotations.readOnlyHint
        assert tools["get_neighbors"].annotations.readOnlyHint
        await mcp.call_tool("get_node", {"snapshotId": "s", "nodeId": "method"})
        await mcp.call_tool("get_neighbors", {"snapshotId": "s", "nodeId": "method", "direction": "outgoing", "limit": 7})
        assert requests[0].url.path == "/api/projects/selected/graph-node"
        assert requests[1].url.path == "/api/projects/selected/graph-neighbors"
        assert json.loads(requests[0].content) == {"snapshotId": "s", "nodeId": "method"}
        assert json.loads(requests[1].content) == {"snapshotId": "s", "nodeId": "method", "direction": "outgoing", "limit": 7}
        for name in ("get_node", "get_neighbors"):
            with pytest.raises(Exception):
                await mcp.call_tool(name, {"projectId": "other", "snapshotId": "s", "nodeId": "method"})
            with pytest.raises(Exception):
                await mcp.call_tool(name, {"nodeId": "method"})
        assert len(requests) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("source_kind", ["snapshot-captured-source", "working-tree"])
def test_description_byte_fallback_points_to_captured_graph_without_live_provenance(monkeypatch, source_kind):
    """Сокращённое описание направляет к graph tools и не выдаёт live-факты за captured."""
    original = httpx.AsyncClient
    description = {"attributeFacts": {"sourceKind": source_kind, "sourceHash": "hash"},
                   "factualSummary": "Факты", "documentation": "😀" * 17_000,
                   "comment": "Назначение метода.", "commentScope": "declaration",
                   "commentSource": {"sourceKind": "working-tree", "sourceHash": "current"}}
    assert len(json.dumps(description, ensure_ascii=False)) < MAX_RESPONSE_BYTES
    assert byte_size(description) > MAX_RESPONSE_BYTES

    def client(**options):
        """Возвращает реальный большой UTF-8 response через транспорт HTTPX."""
        return original(**options, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=description)))

    monkeypatch.setattr(agent_mcp.httpx, "AsyncClient", client)

    async def scenario():
        """Проверяет SDK structured response; JSON textblock сохраняет совместимость."""
        mcp = agent_mcp.create_mcp(8877, project_id="selected")
        response = await mcp.call_tool("describe_node", {"snapshotId": "s", "nodeId": "method"})
        result = response[1] if isinstance(response, tuple) else json.loads(response[0].text)
        assert byte_size(result) <= MAX_RESPONSE_BYTES and result["truncated"]
        assert "get_node(snapshotId,nodeId)" in result["warning"]
        assert "get_neighbors(snapshotId,nodeId)" in result["warning"]
        assert result["attributeFactsAvailable"] is (source_kind == "snapshot-captured-source")
        assert result["comment"] == description["comment"]
        assert result["commentSource"] == description["commentSource"]
        if source_kind == "snapshot-captured-source":
            assert result["attributeFactsProvenance"] == {"sourceKind": source_kind, "sourceHash": "hash"}
        else:
            assert "attributeFactsProvenance" not in result

    asyncio.run(scenario())


def test_mcp_search_comments_respect_utf8_response_budget(monkeypatch):
    """Добавленные пояснения не увеличивают MCP-ответ выше byte-бюджета и не меняют total."""
    original = httpx.AsyncClient
    search = {"nodes": [{"id": str(index), "label": "Method", "comment": "я" * 2000}
                        for index in range(30)], "total": 30, "snapshotId": "s", "truncated": False}

    def client(**options):
        """Возвращает большую страницу с захваченными русскими пояснениями."""
        return original(**options, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=search)))

    monkeypatch.setattr(agent_mcp.httpx, "AsyncClient", client)

    async def scenario():
        """SDK получает уменьшенную страницу с явным признаком усечения."""
        response = await agent_mcp.create_mcp(8877, project_id="selected").call_tool("search_nodes", {"query": "Method"})
        result = response[1] if isinstance(response, tuple) else json.loads(response[0].text)
        assert byte_size(result) <= MAX_RESPONSE_BYTES
        assert result["truncated"] and result["total"] == 30
        assert 0 < len(result["nodes"]) < 30
        assert result["nodes"][0]["comment"] == "я" * 2000

    asyncio.run(scenario())


def test_saved_view_comments_respect_utf8_response_budget(monkeypatch):
    """Сохранённый вид с длинными комментариями возвращает ссылку вместо превышения MCP-бюджета."""
    original = httpx.AsyncClient
    view = {"id": "view", "title": "Comments", "projectId": "selected", "snapshotId": "s", "createdAt": "now",
            "metadata": {}, "url": "/view.html?id=view", "pngUrl": "/image.png", "edges": [], "annotations": [],
            "nodes": [{"id": str(index), "label": "Method", "comment": "я" * 2000} for index in range(30)]}

    def client(**options):
        """Подставляет сохранённую неизменяемую модель с большим UTF-8 текстом."""
        return original(**options, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=view)))

    monkeypatch.setattr(agent_mcp.httpx, "AsyncClient", client)

    async def scenario():
        """Проверяет сохранение счётчиков и ссылки на полную модель при усечении."""
        response = await agent_mcp.create_mcp(8877, project_id="selected").call_tool("get_view", {"viewId": "view"})
        result = response[1] if isinstance(response, tuple) else json.loads(response[0].text)
        assert byte_size(result) <= MAX_RESPONSE_BYTES
        assert result["truncated"] and result["nodeCount"] == 30
        assert result["apiUrl"] == "http://127.0.0.1:8877/api/views/view"

    asyncio.run(scenario())
