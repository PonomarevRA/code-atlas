"""HTTP wiki принимает opaque IDs и ограниченные страницы, не filesystem пути."""
from fastapi.testclient import TestClient
from atlas.server import create_app
from atlas.store import StoreError


def test_wiki_routes_transport_and_validation(tmp_path, monkeypatch):
    """Проверяет wiring, лимиты и передачу stale/unknown status сервисом."""
    app = create_app(tmp_path)
    seen = []

    def node(project, snapshot, identifier, limit, *, include_recommendations=True):
        """Изолирует транспорт от ONNX и сохраняет переданные ограничения."""
        seen.append((project, snapshot, identifier, limit, include_recommendations))
        if snapshot == "stale":
            raise StoreError("Карта обновилась", 409)
        return {"projectId": project, "snapshotId": snapshot, "node": {"id": identifier}}

    def document(project, snapshot, identifier, offset, maximum):
        """Документ выбирается по captured ID, а не по пути."""
        seen.append((project, snapshot, identifier, offset, maximum))
        if identifier == "missing":
            raise StoreError("Не найден", 404)
        return {"text": "Captured", "snapshotId": snapshot}

    monkeypatch.setattr(app.state.wiki, "node_wiki", node)
    monkeypatch.setattr(app.state.wiki, "document", document)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        wiki = "/api/projects/project/node-wiki"
        doc = "/api/projects/project/wiki-document"
        assert client.post(wiki, json={"nodeId": "raw", "snapshotId": "s"}).status_code == 200
        assert seen[-1] == ("project", "s", "raw", 8, True)
        assert client.post(wiki, json={"nodeId": "raw", "snapshotId": "s", "includeRecommendations": False}).status_code == 200
        assert seen[-1] == ("project", "s", "raw", 8, False)
        assert client.post(wiki, json={"nodeId": "raw", "snapshotId": "s", "includeRecommendations": True}).status_code == 200
        assert seen[-1] == ("project", "s", "raw", 8, True)
        assert client.post(wiki, json={"nodeId": "raw", "snapshotId": "stale"}).status_code == 409
        assert client.post(doc, json={"documentId": "doc", "snapshotId": "s", "offset": 12000}).status_code == 200
        assert seen[-1] == ("project", "s", "doc", 12000, 12000)
        assert client.post(doc, json={"documentId": "missing", "snapshotId": "s"}).status_code == 404
        for extra in [{"path": "/etc/passwd"}, {"offset": -1}, {"offset": True}, {"maxChars": 12001}, {"documentId": "x" * 257}]:
            assert client.post(doc, json={"documentId": "doc", "snapshotId": "s", **extra}).status_code == 422
        for limit in [0, 13, True]:
            assert client.post(wiki, json={"nodeId": "raw", "snapshotId": "s", "limit": limit}).status_code == 422
        for value in [0, 1, "true", "false", None, [], {}]:
            count = len(seen)
            assert client.post(wiki, json={"nodeId": "raw", "snapshotId": "s", "includeRecommendations": value}).status_code == 422
            assert len(seen) == count
