"""Проекция карты сохраняет смысл графа и полный HTTP-контракт."""
import json
from copy import deepcopy
from fastapi.testclient import TestClient
from atlas import server, store
from atlas.map_payload import serialize_graph
from test_grouping_cache import legacy_store


def test_map_projection_keeps_evidence_comments_and_original_snapshot():
    """Исключает только подробные attributeFacts, оставляя поля, документацию и рёбра."""
    graph = {"nodes": [{"id": "a", "label": "Метод", "attributeFacts": [{"name": "Route"}],
                        "fieldFacts": [{"name": "Value"}], "comment": "Назначение реализации"}],
             "edges": [{"source": "a", "target": "a", "evidence": "source:20"}],
             "metadata": {"snapshotId": "one"}}
    original = deepcopy(graph)
    assert json.loads(serialize_graph(graph)) == original
    projected = json.loads(serialize_graph(graph, "map"))
    assert projected["nodes"][0] == {key: value for key, value in graph["nodes"][0].items() if key != "attributeFacts"}
    assert projected["edges"] == original["edges"]
    assert projected["metadata"]["snapshotId"] == "one"
    assert projected["metadata"]["projection"]["omittedNodeFields"] == ["attributeFacts", "testFacts", "entrypointFacts"]
    assert graph == original
    assert "Метод".encode() in serialize_graph(graph)


def test_graph_http_projection_is_optional_and_does_not_change_snapshot(tmp_path):
    """Старые клиенты получают все факты; карта opt-in, неверная проекция отвергается."""
    state, project_id, path, graph = legacy_store(tmp_path)
    graph["nodes"][0]["attributeFacts"] = [{"name": "Route", "comment": "Комментарий"}]
    store.write_json(path, graph)
    original_bytes = path.read_bytes()
    state.close()
    with TestClient(server.create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        endpoint = f"/api/projects/{project_id}/graph"
        full = client.get(endpoint)
        reduced = client.get(endpoint, params={"projection": "map"})
        assert full.status_code == reduced.status_code == 200
        assert full.headers["content-type"] == "application/json"
        assert full.json()["nodes"][0]["attributeFacts"] == graph["nodes"][0]["attributeFacts"]
        assert "attributeFacts" not in reduced.json()["nodes"][0]
        assert reduced.json()["edges"] == full.json()["edges"]
        assert client.get(endpoint, params={"projection": "summary"}).status_code == 422
    assert path.read_bytes() == original_bytes
