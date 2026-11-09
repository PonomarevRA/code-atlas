"""Общий снимок, изоляция и API пути получают одинаковые связи реализаций."""

import json
from copy import deepcopy
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from atlas import store, enrichment_pipeline
from atlas.grouping import enrich_graph
from atlas.server import create_app
from test_jobs import add_project, fake_build, wait_for_job


def test_interface_edges_are_published_and_failed_enrichment_preserves_snapshot(tmp_path, source_project, monkeypatch):
    """Обогащение предшествует публикации; его ошибка сохраняет рабочую ревизию и raw Graphify."""
    monkeypatch.setattr(store, "build_graph", fake_build)

    def local_grouping(graph):
        """Keep publication metadata while avoiding the unrelated external Graphistry worker."""
        return enrich_graph(graph, provider_override=lambda topology: [set(topology.nodes)])

    def unexpected_grouping_worker(*args, **kwargs):
        """This publication-contract test must not start a separate grouping process."""
        raise AssertionError("External Graphistry grouping is outside this test's scope")

    monkeypatch.setattr(store, "enrich_graph", local_grouping)
    monkeypatch.setattr("atlas.graphistry_provider._run_worker", unexpected_grouping_worker)

    def resolved_graph(graph, root):
        """Подменяет анализ типов, чтобы проверить отдельно атомарный контракт хранилища."""
        assert root == source_project
        result = deepcopy(graph)
        result["nodes"].append({"id": "implementation", "label": "Run()", "kind": "symbol", "file": "Calculator.cs"})
        result["edges"].append({"source": "first", "target": "implementation", "relation": "dispatches_to",
                                "confidence": "INFERRED", "_origin": "interface-dispatch"})
        result["metadata"]["interfaceDispatch"] = {"version": "fixture", "derivedDispatch": 1}
        return result

    monkeypatch.setattr(enrichment_pipeline, "enrich_interface_dispatch", resolved_graph)
    app = create_app(tmp_path / "state")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        project_id, job = add_project(client, source_project)
        assert job["status"] == "completed"
        # A successful publication schedules a legitimate health overlay. Compare
        # graph responses only after that derivative has settled, as in test_jobs.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            refresh = client.get(f"/api/projects/{project_id}/code-health/refresh").json()
            if refresh["status"] == "completed":
                break
            time.sleep(.01)
        else:
            pytest.fail(f"Initial health refresh did not finish: {refresh}")
        graph = client.get(f"/api/projects/{project_id}/graph").json()
        assert graph["metadata"]["groupingProvider"] == "graphistry"
        assert graph["metadata"]["interfaceDispatch"]["derivedDispatch"] == 1
        assert any(edge["relation"] == "dispatches_to" for edge in graph["edges"])
        path = client.post(f"/api/projects/{project_id}/execution-path", json={
            "nodeId": "first", "snapshotId": graph["metadata"]["snapshotId"], "maxNodes": 100, "maxDepth": 8,
        })
        assert path.status_code == 200, path.text
        assert {step["nodeId"] for step in path.json()["steps"]} == {"first", "implementation"}
        raw_path = Path(app.state.store.projects[project_id]["_rawGraph"])
        raw_bytes = raw_path.read_bytes()
        assert json.loads(raw_bytes)["edges"] == []

        def failed_resolution(*args):
            """Воспроизводит ошибку нового анализатора при повторной сборке."""
            raise RuntimeError("interface fixture failure")

        monkeypatch.setattr(enrichment_pipeline, "enrich_interface_dispatch", failed_resolution)
        response = client.post(f"/api/projects/{project_id}/rebuild", json={})
        failed = wait_for_job(client, response.json()["job"]["id"])
        assert failed["status"] == "failed"
        assert client.get(f"/api/projects/{project_id}/graph").json() == graph
        assert raw_path.read_bytes() == raw_bytes
