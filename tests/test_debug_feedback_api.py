"""Trust-boundary integration for imported JUnit declarations and captured facts."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from atlas.debug_feedback_api import register_debug_feedback_routes
from atlas.store import StoreError
from test_pattern_module_adapters import Store


XML = '<testsuite tests="999"><testcase name="Fail" file="core/a.py" line="5"><failure message="boom">trace</failure></testcase><testcase name="Skip"><skipped/></testcase></testsuite>'


@pytest.fixture
def feedback_client():
    """Keep source identities host-owned while the report remains caller supplied."""
    state = Store()
    state.graph["nodes"][0].update(label="Fail", line=5, sourceHash="a" * 64)
    state.graph["metadata"]["buildId"] = "build-1"
    app = FastAPI()
    register_debug_feedback_routes(app, state)

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        return JSONResponse({"error": str(error)}, status_code=error.status)

    with TestClient(app) as client:
        yield client, state


def payload(**extra):
    """Make each declared identity explicit, without trusting the XML's aggregate count."""
    return {"snapshotId": "snap", "reportId": "report-1", "junitXml": XML,
            "reportedSnapshotId": "snap", "reportedBuildId": "build-1", **extra}


def test_static_candidate_keeps_reported_execution_separate(feedback_client):
    client, state = feedback_client
    response = client.post("/api/projects/p/debug-feedback", json=payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["executionVerified"] is False and result["resultsPresent"] is True
    assert result["summary"] == {"passed": 0, "failure": 1, "error": 0, "skipped": 1, "unknown": 0}
    assert result["items"][0]["sourceLinkStatus"] == "matched-to-captured-source"
    assert result["items"][0]["sourceRefs"][0]["sourceHash"] == "a" * 64
    assert state.snapshot_id == "snap" and state.graph["nodes"][0]["label"] == "Fail"


@pytest.mark.parametrize("extra,reason", [
    ({"reportedSnapshotId": None}, "report-snapshot-unknown"),
    ({"reportedSnapshotId": "old"}, "report-snapshot-stale"),
    ({"reportedBuildId": "old-build"}, "report-build-stale"),
])
def test_unknown_or_stale_report_cannot_be_silently_linked(feedback_client, extra, reason):
    client, _ = feedback_client
    result = client.post("/api/projects/p/debug-feedback", json=payload(**extra)).json()
    assert reason in result["unknownReasons"]
    assert result["items"][0]["sourceRefs"] == []
    assert result["executionVerified"] is False


def test_no_results_does_not_mean_passing(feedback_client):
    client, _ = feedback_client
    result = client.post("/api/projects/p/debug-feedback", json=payload(junitXml="<testsuite/>")).json()
    assert result["resultsPresent"] is False and result["executionVerified"] is False
    assert "no-testcases" in result["unknownReasons"]


def test_xml_and_source_fact_boundaries(feedback_client):
    client, _ = feedback_client
    assert client.post("/api/projects/p/debug-feedback", json=payload(source_facts=[])).status_code == 422
    assert client.post("/api/projects/p/debug-feedback", json=payload(snapshotId="old")).status_code == 409
    dtd = '<!DOCTYPE testsuite [<!ENTITY x "forbidden">]><testsuite><testcase name="x"><failure>&x;</failure></testcase></testsuite>'
    assert client.post("/api/projects/p/debug-feedback", json=payload(junitXml=dtd)).status_code == 400
    assert client.post("/api/projects/p/debug-feedback", json=payload(junitXml="<testsuite>\x00</testsuite>")).status_code == 400


def test_report_profile_never_promotes_log_markup_or_notrun_to_pass(feedback_client):
    client, state = feedback_client
    logged = '<testsuite><system-out><testcase name="log-only"/></system-out></testsuite>'
    assert client.post("/api/projects/p/debug-feedback", json=payload(junitXml=logged)).status_code == 400
    assert state.reads == 0
    notrun = '<testsuite><testcase name="Fail" status="notrun"/></testsuite>'
    result = client.post("/api/projects/p/debug-feedback", json=payload(junitXml=notrun)).json()
    assert result["summary"]["passed"] == 0 and result["summary"]["unknown"] == 1
    assert result["executionVerified"] is False


def test_masking_cannot_exceed_output_diagnostic_limit(feedback_client):
    client, _ = feedback_client
    text = "api_key=x " * 195
    xml = '<testsuite><testcase name="Fail"><failure>' + text + '</failure></testcase></testsuite>'
    response = client.post("/api/projects/p/debug-feedback", json=payload(junitXml=xml))
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert len(item["details"]) <= 2048
    assert item["detailsTruncated"] is True
