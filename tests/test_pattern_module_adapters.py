"""Contract, snapshot and admission checks across real read-only feature adapters."""

import asyncio
from copy import deepcopy

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from atlas.code_patterns import PatternCatalog
from atlas.map_maintenance import MapMaintenance
from atlas.module_analysis_api import register_module_analysis_routes
from atlas.module_analysis_mcp import register_module_analysis_tools
from atlas.module_analysis_service import ModuleAnalysisService
from atlas.pattern_catalog_api import register_pattern_routes
from atlas.pattern_catalog_mcp import register_pattern_tools
from atlas.store import StoreError


class Store:
    """Keep one source fixture immutable and expose the existing snapshot contract."""

    def __init__(self):
        """Prepare complete captured dependency facts and a publication revision."""
        self.snapshot_id = "snap"
        self.maintenance = MapMaintenance()
        self.graph = {
            "nodes": [
                {"id": "a", "kind": "method", "file": "core/a.py"},
                {"id": "b", "kind": "method", "file": "io/b.py"},
            ],
            "edges": [{"source": "a", "target": "b", "relation": "imports", "confidence": "EXTRACTED"}],
            "metadata": {"analysisStatus": "complete"},
        }
        self.reads = 0

    def assert_snapshot(self, project_id, snapshot_id):
        """Reject another project or a source that changed during analysis."""
        if project_id != "p":
            raise StoreError("Project not found", 404)
        if snapshot_id != self.snapshot_id:
            raise StoreError("Snapshot changed", 409)

    def comparison_snapshot(self, project_id):
        """Return the shared source so mutation would be detectable by the test."""
        self.reads += 1
        return None, self.graph


@pytest.fixture
def feature_client():
    """Exercise real catalogue and engine validation behind FastAPI contracts."""
    app = FastAPI()
    state = Store()
    service = ModuleAnalysisService(state, concurrency=1)
    register_pattern_routes(app, PatternCatalog())
    register_module_analysis_routes(app, service)

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        return JSONResponse({"error": str(error)}, status_code=error.status)

    with TestClient(app) as client:
        yield client, state, service


def test_pattern_exact_identity_paging_and_stale_reference(feature_client):
    client, _, _ = feature_client
    first = client.get("/api/code-patterns", params={"limit": 1}).json()
    assert first["total"] == 12 and first["nextOffset"] == 1
    summary = first["items"][0]
    path = "/api/code-patterns/" + summary["id"]
    card = client.get(path, params={
        "version": summary["version"], "contentHash": summary["contentHash"],
        "catalogVersion": first["catalogVersion"],
    })
    assert card.status_code == 200
    assert card.json()["pattern"]["contentHash"] == summary["contentHash"]
    assert card.json()["consumerQualification"]["status"] == "not-assessed"
    assert client.get(path, params={"contentHash": "0" * 64}).status_code == 409
    assert client.get("/api/code-patterns", params={"limit": 21}).status_code == 422
    assert client.get("/api/code-patterns", params={"catalogVersion": "stale"}).status_code == 409


def test_module_contract_preserves_source_and_reports_providers(feature_client):
    client, state, _ = feature_client
    original = deepcopy(state.graph)
    response = client.post("/api/projects/p/module-analysis", json={
        "snapshotId": "snap", "algorithms": ["current", "sarf-simple-directed"],
    })
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["readOnly"] is True
    assert report["projection"]["unitTotal"] == 2
    assert [p["algorithm"] for p in report["comparison"]["proposals"]] == ["current", "sarf-simple-directed"]
    assert state.graph == original and state.maintenance.requests == 0
    assert client.post("/api/projects/p/module-analysis", json={"snapshotId": "old"}).status_code == 409
    assert client.post("/api/projects/other/module-analysis", json={"snapshotId": "snap"}).status_code == 404


@pytest.mark.parametrize("extra", [{"seed": True}, {"resolution": True}, {"algorithms": ["unknown"]}, {"extra": 1},
                                  {"folders": ["../outside"]}, {"folders": ["core", "core/sub"]}])
def test_module_input_is_rejected_before_publication(feature_client, extra):
    client, state, _ = feature_client
    response = client.post("/api/projects/p/module-analysis", json={"snapshotId": "snap", **extra})
    assert response.status_code in (400, 422)
    assert state.snapshot_id == "snap" and state.maintenance.requests == 0


def test_module_admission_and_generation_fence(feature_client):
    client, state, service = feature_client
    assert service.slots.acquire(blocking=False)
    try:
        assert client.post("/api/projects/p/module-analysis", json={"snapshotId": "snap"}).status_code == 429
    finally:
        service.slots.release()
    original = state.comparison_snapshot

    def replaced(project_id):
        """Simulate publication after the graph was captured."""
        result = original(project_id)
        state.snapshot_id = "new"
        return result

    state.comparison_snapshot = replaced
    assert client.post("/api/projects/p/module-analysis", json={"snapshotId": "snap"}).status_code == 409
    assert state.maintenance.requests == 0


def test_mcp_proxy_binds_project_and_encodes_exact_identity():
    class MCP:
        """Capture tools while retaining their real asynchronous proxy bodies."""
        def __init__(self):
            self.tools = {}
        def tool(self, **kwargs):
            def register(function):
                self.tools[function.__name__] = function
                return function
            return register

    server, calls = MCP(), []

    async def request(method, path, body=None):
        calls.append((method, path, body))
        return {"readOnly": True}

    def selected(project_id):
        if project_id not in (None, "p"):
            raise ValueError("Wrong project")
        return "p"

    register_pattern_tools(server, request)
    register_module_analysis_tools(server, request, selected)
    asyncio.run(server.tools["search_code_patterns"]("class folder", catalogVersion="hash"))
    assert "query=class+folder" in calls[-1][1] and "catalogVersion=hash" in calls[-1][1]
    asyncio.run(server.tools["get_code_pattern"]("ports-and-adapters", contentHash="h"))
    assert calls[-1][1].endswith("ports-and-adapters?contentHash=h")
    with pytest.raises(ValueError):
        asyncio.run(server.tools["analyze_code_modules"]("snap", projectId="other"))
    before = len(calls)
    asyncio.run(server.tools["analyze_code_modules"]("snap", folders=["core"]))
    assert len(calls) == before + 1
    assert calls[-1][1] == "/api/projects/p/module-analysis"
    assert calls[-1][2]["folders"] == ["core"]
