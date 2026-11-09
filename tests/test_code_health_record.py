"""Continuation preserves retained identities, estimates and source uncertainty."""

from copy import deepcopy
import asyncio
import json
import os
import sys

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from atlas.code_health_record_page import health_record_page
from atlas.code_health_service import CodeHealthService
from atlas.code_health_models import HealthQuery
from atlas.server import create_app
from test_code_health import health_store
from test_agent_mcp import atlas_server, unpack


def test_pages_deliver_each_identity_once_with_unicode_budget():
    """Large rows shrink page size without skipping any retained method."""
    nodes = [{"healthIdentity": str(i), "comment": "данные" * 1800} for i in range(11)]
    record = {"assessmentId": "a" * 32, "nodes": nodes, "truncated": True, "status": "partial"}
    original = deepcopy(record)
    offset, seen = 0, []
    for _ in range(12):
        page = health_record_page(record, "a" * 32, "assessment", offset, 10, "/record")
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 64000
        assert page["sourceScopeTruncated"] is True and page["status"] == "partial"
        seen.extend(item["healthIdentity"] for item in page["items"])
        if page["nextOffset"] is None:
            break
        assert page["nextOffset"] > offset
        offset = page["nextOffset"]
    assert seen == [str(i) for i in range(11)]
    assert record == original


def test_oversized_row_and_metadata_never_stall_or_hide_estimation():
    """An abbreviated row retains identity and status with a link to all stored facts."""
    record = {"planId": "b" * 32, "assessmentKind": "estimated", "summary": {"large": "x" * 80000},
              "projections": [{"identity": "method", "reason": "x" * 100000}, {"identity": "next"}]}
    page = health_record_page(record, "b" * 32, "plan", 0, 1, "/record?kind=plan")
    assert page["assessmentKind"] == "estimated" and page["metadataTruncated"]
    assert page["items"][0]["identity"] == "method" and page["items"][0]["itemTruncated"]
    assert page["nextOffset"] == 1 and len(json.dumps(page).encode()) < 64000
    assert health_record_page(record, "b" * 32, "plan", 1, 1, "/record")["nextOffset"] is None


def test_adversarial_metadata_and_unicode_identifiers_still_progress():
    """Retained records with unexpectedly large schema fields cannot defeat page bounds."""
    row = {key: "😀" * 5000 for key in ("id", "identity", "nodeId", "file", "label", "sourceHash")}
    record = {"freshness": {"status": "current", "details": "x" * 70000},
              "nodes": [row, {"healthIdentity": "next"}]}
    page = health_record_page(record, "a" * 32, "assessment", 0, 1, "/record")
    assert page["metadataTruncated"] and page["freshness"]["status"] == "current"
    assert page["items"][0]["itemTruncated"] and page["nextOffset"] == 1
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 64000


def test_control_character_escaping_cannot_make_abbreviated_row_stall():
    """JSON expansion is budgeted too, rather than assuming one byte per source character."""
    row = {key: "\x01" * 5000 for key in ("id", "healthIdentity", "identity", "nodeId", "file", "label", "line", "sourceHash")}
    for section in ("health", "complexity", "documentationHealth"):
        row[section] = {key: "\x01" * 5000 for key in
                        ("score", "status", "policyVersion", "analyzerVersion", "sourceHash")}
    page = health_record_page({"nodes": [row, {}]}, "a" * 32, "assessment", 0, 1, "/record")
    assert len(page["items"]) == 1 and page["nextOffset"] == 1
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 64000


def test_http_pages_are_immutable_and_project_scoped(health_store):
    """Deleting current source cannot erase a baseline or make a plan measured."""
    state, project, _, root = health_store
    service = CodeHealthService(state)
    baseline = service.assess(project, HealthQuery(sourceMode="working-tree", files=["amount.py"]))
    (root / "amount.py").unlink()
    app = create_app(state.data_dir)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        url = f'/api/projects/{project}/code-health/records/{baseline["assessmentId"]}/page'
        response = client.get(url)
        assert response.status_code == 200
        page = response.json()
        assert page["items"][0]["healthIdentity"] == baseline["nodes"][0]["healthIdentity"]
        assert page["measuredAt"] == baseline["measuredAt"]
        assert client.get(url, params={"kind": "plan"}).json()["verdict"] == "UNKNOWN"
        assert client.get(url, params={"limit": 21}).status_code == 422
        assert client.get(url, params={"offset": -1}).status_code == 422
        assert client.get(url.replace(project, "missing-project")).status_code == 404


def test_record_tool_real_stdio_contract_and_continuation(atlas_server):
    """The real MCP proxy exposes strict bounds and retrieves a retained baseline."""
    port, _ = atlas_server

    async def scenario():
        """Use the same handshake/discovery path as an editor client."""
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "atlas.agent_mcp", "--port", str(port)], env=dict(os.environ))
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                catalog = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert catalog["get_code_health_record"].annotations.readOnlyHint is True
                baseline = unpack(await session.call_tool("get_code_health", {
                    "projectId": "legacy-fixture", "sourceMode": "working-tree", "files": ["health_fixture.py"]}))
                page = unpack(await session.call_tool("get_code_health_record", {
                    "projectId": "legacy-fixture", "recordId": baseline["assessmentId"], "limit": 1}))
                assert page["items"][0]["healthIdentity"] == baseline["nodes"][0]["healthIdentity"]
                assert page["nextOffset"] is None and page["assessmentKind"] == "measured"
                assert page["recordUrl"].startswith(f"http://127.0.0.1:{port}/api/projects/")
                missing = unpack(await session.call_tool("get_code_health_record", {
                    "projectId": "legacy-fixture", "recordId": "0" * 32}))
                assert missing["verdict"] == "UNKNOWN"
                rejected = await session.call_tool("get_code_health_record", {
                    "projectId": "legacy-fixture", "recordId": baseline["assessmentId"], "offset": True})
                assert rejected.isError

    asyncio.run(scenario())


def test_oversized_row_retains_independent_coverage_and_unknown_reasons():
    """Transport truncation keeps graph uncertainty visible without changing the immutable record."""
    coverage = {'inventory': {'status': 'complete', 'reason': 'captured-complete-facts', 'scope': 'a.py'},
                'cycles': {'status': 'partial', 'reason': 'absence-not-proven-on-partial-graph', 'scope': 'selected-source-graph'}}
    node = {'id': 'a', 'file': 'a.py', 'comment': 'x' * 100000,
            'health': {'score': None, 'organizationScore': None, 'status': 'partial',
                       'componentCoverage': {'flow': {'status': 'complete', 'reason': 'local-facts', 'scope': 'local'},
                                             'dependencies': {'status': 'unknown', 'reason': 'unresolved-calls', 'scope': 'static-graph'}},
                       'organization': {'version': 'code-organization-v2', 'status': 'partial', 'score': None, 'coverage': coverage}}}
    record = {'nodes': [node]}
    original = deepcopy(record)
    page = health_record_page(record, 'a' * 32, 'assessment', 0, 1, '/record')
    retained = page['items'][0]
    assert retained['itemTruncated']
    assert retained['health']['organization']['coverage'] == coverage
    assert retained['health']['componentCoverage']['dependencies']['reason'] == 'unresolved-calls'
    assert retained['health']['score'] is None
    assert record == original


def test_adversarial_coverage_is_byte_bounded_and_pages_advance():
    """Unexpected retained text cannot break the one-item progress guarantee."""
    bad = {'status': '\x01' * 5000, 'reason': '\x01' * 5000, 'scope': '\x01' * 5000}
    node = {'id': 'a', 'comment': 'x' * 100000,
            'health': {'componentCoverage': {key: bad for key in ('flow', 'size', 'dependencies', 'documentation', 'organization')},
                       'organization': {'coverage': {key: bad for key in ('inventory', 'local', 'outgoing', 'incoming', 'cycles', 'fragmentation')}}}}
    page = health_record_page({'nodes': [node, {}]}, 'a' * 32, 'assessment', 0, 1, '/record')
    assert len(page['items']) == 1 and page['nextOffset'] == 1
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 64000
