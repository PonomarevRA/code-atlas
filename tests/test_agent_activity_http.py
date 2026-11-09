"""Portable HTTP-only retrieval: strict inputs, coherent cursors, projection and bounded pages."""

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from atlas.agent_activity.http_api import normalized_health
from atlas.agent_activity.http_query import EventQuery
from test_agent_activity import client_for, event, journal

BASE = "/api/agent-activity/v1"


def persist(journal, rows):
    """Seeds isolated retained events in batches small enough for the producer byte quota."""
    for offset in range(0, len(rows), 20):
        for row in rows[offset:offset + 20]:
            assert journal.submit(row)
        assert journal.flush()


def continuation(page, direction):
    """Builds the next exclusive request using the frozen common cursor contract."""
    return {"journalEpoch": page["nextCursor"]["journalEpoch"],
            "afterSequence" if direction == "forward" else "beforeSequence": page["nextCursor"]["sequence"]}


def test_http_status_is_normalized_and_read_only(journal):
    persist(journal, [event()])
    with client_for(journal) as client:
        before = journal.status()["cursor"]
        response = client.get(BASE + "/status")
        status = response.json()
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"].startswith("application/json")
        assert status["schemaVersion"] == "agent-activity-http/v1"
        assert status["application"] == "atlas" and status["eventSchemaVersion"] == "agent-activity/v1"
        assert status["limits"] == {"maxPageEvents": 100, "maxPageBytes": 262144}
        assert status["cursor"] == before and status["oldestSequence"] == "1"
        assert status["health"] == {"status": "ok", "droppedTotal": 0, "queueItems": 0, "queueBytes": 0, "errorCode": None}
        assert client.get(BASE + "/events").status_code == 200
        assert journal.status()["cursor"] == before


@pytest.mark.parametrize("query", ["unknown=x", "limit=1&limit=2", "q=a&q=b", "limit=0", "limit=101", "limit=01",
    "limit=+1", "limit=1.0", "limit=", "Limit=1", "journalEpoch=epoch", "afterSequence=0", "beforeSequence=0",
    "journalEpoch=&afterSequence=0", "journalEpoch=e&afterSequence=0&beforeSequence=0", "journalEpoch=e&afterSequence=01",
    "journalEpoch=e&afterSequence=-1", "journalEpoch=e&afterSequence=9223372036854775808", "q=%ED%A0%80", "q=%FF", "q=%GG", "q=%", "q=%80", "q=%0", "l%69mit=1&limit=2"])
def test_strict_query_errors_are_safe_and_uniform(journal, query):
    with client_for(journal) as client:
        response = client.get(BASE + "/events?" + query)
        assert response.status_code == 400
        assert response.json() == {"schemaVersion": "agent-activity-http/v1", "application": "atlas",
            "error": {"code": "invalid_query", "message": "Invalid query parameters.", "retryable": False}, "resetCursor": None}
        assert response.headers["cache-control"] == "no-store"


def test_status_accepts_no_query_and_boundary_rejects_remote_peer(journal):
    with client_for(journal) as client:
        assert client.get(BASE + "/status?limit=1").json()["error"]["code"] == "invalid_query"
        assert client.get(BASE + "/status?x=&x=").status_code == 400
        assert client.get(BASE + "/status?q=%GG").status_code == 400
        assert client.get(BASE + "/status?%71=x&q=y").status_code == 400
        foreign = client.get(BASE + "/events", headers={"Origin": "https://attacker.invalid"})
        assert foreign.status_code == 403
        assert client.get(BASE + "/events", headers={"Origin": "http://127.0.0.1"}).status_code == 200
        with TestClient(client.app, base_url="http://127.0.0.1", client=("192.0.2.1", 5)) as remote:
            response = remote.get(BASE + "/events")
            assert response.status_code == 403
            assert response.json()["error"] == {"code": "forbidden", "message": "Access forbidden.", "retryable": False}


def test_forward_backward_pages_have_exclusive_boundaries_and_independent_head(journal):
    persist(journal, [event(str(index)) for index in range(7)])
    with client_for(journal) as client:
        initial = client.get(BASE + "/events?limit=3").json()
        assert [row["sequence"] for row in initial["events"]] == ["5", "6", "7"]
        assert initial["nextCursor"]["sequence"] == "5" and initial["highWatermark"]["sequence"] == "7"
        assert initial["hasMore"] and not initial["truncated"]
        second = client.get(BASE + "/events", params={"limit": 3, **continuation(initial, "backward")}).json()
        assert [row["sequence"] for row in second["events"]] == ["2", "3", "4"]
        third = client.get(BASE + "/events", params={"limit": 3, **continuation(second, "backward")}).json()
        assert [row["sequence"] for row in third["events"]] == ["1"]
        assert third["nextCursor"]["sequence"] == "0" and not third["hasMore"]
        forward = client.get(BASE + "/events", params={"limit": 3, **continuation(third, "forward")}).json()
        assert [row["sequence"] for row in forward["events"]] == ["1", "2", "3"]
        seen = [row["sequence"] for row in forward["events"]]
        while forward["hasMore"]:
            forward = client.get(BASE + "/events", params={"limit": 3, **continuation(forward, "forward")}).json()
            seen.extend(row["sequence"] for row in forward["events"])
        assert seen == [str(index) for index in range(1, 8)]
        assert forward["nextCursor"] == forward["highWatermark"]


def test_literal_filters_match_only_common_public_fields_and_preserve_legacy_search(journal):
    rows = [event("Needle", sessionId="session-1"), event("needle", sessionId="session-2"),
            event("unrelated", operation="NeedleOperation"),
            event("unrelated", request={"tool": "search_nodes", "summary": "Request Needle"}),
            event("unrelated", response={"summary": "Response Needle", "items": [{"label": "hiddenWord"}]}),
            event("other", clientKind="Needle")]
    persist(journal, rows)
    with client_for(journal) as client:
        selected = client.get(BASE + "/events", params={"q": "Needle"}).json()["events"]
        assert [row["sequence"] for row in selected] == ["1", "3", "4", "5"]
        assert client.get(BASE + "/events?q=hiddenWord").json()["events"] == []
        assert [row["sequence"] for row in client.get(BASE + "/events?sessionId=session-2").json()["events"]] == ["2"]
        assert len(client.get(BASE + "/events?operation=search").json()["events"]) == 5
        assert client.get(BASE + "/events?operation=Search").json()["events"] == []
        assert len(client.get("/api/agent-activity/history?q=NEEDLE").json()["events"]) == 2
        assert len(client.get(BASE + "/events?sessionId=&operation=&projectId=&q=").json()["events"]) == 6


def test_unicode_scalar_limits_and_max_signed_sequence_are_portable(journal):
    with client_for(journal) as client:
        assert client.get(BASE + "/events", params={"sessionId": "😀" * 128, "q": "😀" * 512}).status_code == 200
        assert client.get(BASE + "/events", params={"sessionId": "😀" * 129}).status_code == 400
        assert client.get(BASE + "/events", params={"q": "😀" * 513}).status_code == 400
        epoch = journal.status()["journalEpoch"]
        assert client.get(BASE + "/events", params={"journalEpoch": epoch, "afterSequence": "9223372036854775807"}).status_code == 409
    request = SimpleNamespace(scope={"query_string": b"q=\xed\xa0\x80"})
    with pytest.raises(UnicodeError):
        EventQuery.parse(request)


def test_empty_filtered_forward_advances_and_catches_future_matching_events(journal):
    persist(journal, [event("irrelevant") for _ in range(4)])
    with client_for(journal) as client:
        epoch = journal.status()["journalEpoch"]
        page = client.get(BASE + "/events", params={"journalEpoch": epoch, "afterSequence": 0, "q": "wanted"}).json()
        assert page["events"] == [] and not page["hasMore"] and page["nextCursor"]["sequence"] == "4"
        persist(journal, [event("wanted")])
        following = client.get(BASE + "/events", params={"q": "wanted", **continuation(page, "forward")}).json()
        assert [row["sequence"] for row in following["events"]] == ["5"]


def test_epoch_expired_and_rotated_prefix_return_reset_not_silent_empty(journal):
    persist(journal, [event()])
    old = journal.status()["cursor"]
    journal.clear()
    with client_for(journal) as client:
        for field in ("afterSequence", "beforeSequence"):
            response = client.get(BASE + "/events", params={"journalEpoch": old["journalEpoch"], field: old["sequence"]})
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "cursor_expired"
            assert response.json()["resetCursor"] == journal.status()["cursor"]
        persist(journal, [event() for _ in range(4)])
        with journal.disk.lock:
            # Simulate a normally evicted retained prefix using an existing complete segment boundary.
            first = journal.disk.segments[0]
            first["first"] = 3
        response = client.get(BASE + "/events", params={"journalEpoch": journal.disk.epoch, "afterSequence": 0})
        assert response.status_code == 409


@pytest.mark.parametrize("direction", ["forward", "backward"])
def test_byte_limited_mixed_size_pagination_never_skips_matching_events(journal, direction):
    rows = []
    for index in range(110):
        row = event("😀" * (512 if index % 2 else 5))
        if index % 2:
            row["request"] = {"tool": "search_nodes", "summary": "😀" * 512}
            row["response"] = {"summary": "😀" * 512, "truncated": True}
        rows.append(row)
    persist(journal, rows)
    with client_for(journal) as client:
        parameters = {"limit": 100}
        if direction == "forward":
            parameters.update(journalEpoch=journal.disk.epoch, afterSequence=0)
        seen, truncated = [], False
        while True:
            response = client.get(BASE + "/events", params=parameters)
            assert response.status_code == 200
            assert len(response.content) <= 262144
            page = response.json()
            assert page["direction"] == direction
            numbers = [int(row["sequence"]) for row in page["events"]]
            assert numbers == sorted(numbers)
            seen.extend(numbers)
            truncated |= page["truncated"]
            if not page["hasMore"]:
                break
            parameters = {"limit": 100, **continuation(page, direction)}
        assert truncated
        assert sorted(seen) == list(range(1, 111))
        assert len(seen) == len(set(seen))


def test_projection_of_removed_project_and_internal_fields_never_leaks(journal):
    row = event()
    row["target"]["projectId"] = "removed-project"
    persist(journal, [row])
    with client_for(journal) as client:
        response = client.get(BASE + "/events?projectId=removed-project")
        records = response.json()["events"]
        assert len(records) == 1 and records[0]["target"] == {"kind": "code", "status": "unavailable"}
        assert "_crc32" not in response.text and "partition" not in response.text
        assert client.get(BASE + "/events?projectId=other").json()["events"] == []


def test_storage_faults_are_503_with_no_input_or_exception_leaks(journal, monkeypatch):
    def failed_status():
        """Injects private filesystem details which must remain server-side."""
        raise OSError("private-storage-path /secret")
    with client_for(journal) as client:
        monkeypatch.setattr(journal, "status", failed_status)
        for route in ("/status", "/events?q=private-query"):
            response = client.get(BASE + route)
            assert response.status_code == 503
            assert response.json()["error"] == {"code": "journal_unavailable", "message": "Journal is unavailable.", "retryable": True}
            assert response.json()["resetCursor"] is None
            assert "private-" not in response.text


def test_unknown_normalized_health_values_are_explicit_null():
    assert normalized_health({"health": {"status": "degraded", "errorCode": "unavailable"}}) == {
        "status": "degraded", "droppedTotal": None, "queueItems": None, "queueBytes": None, "errorCode": "unavailable"}


def test_shared_atlas_golden_status_pages_and_errors_match_actual_mappers():
    """Uses canonical shared examples rather than language-local expected envelopes."""
    from threading import RLock
    from atlas.agent_activity.api import project_event
    from atlas.agent_activity.disk import _matches_query
    from atlas.agent_activity.http_api import error_response, event_page, versioned_status
    golden = json.loads(Path("contracts/agent-activity/http-v1/examples.json").read_text())
    case = next(item for item in golden["cases"] if item["application"] == "atlas")
    expected = case["status"]
    status = {**expected, "health": {"status": "ok", "dropped": {"known": expected["health"]["droppedTotal"]},
                                    "queueItems": 0, "queueBytes": 0, "errorCode": None}}
    assert versioned_status(status) == expected
    store = SimpleNamespace(lock=RLock(), projects={"example-project": {}})
    for example in case["pages"]:
        query = EventQuery.parse(SimpleNamespace(scope={"query_string": example["query"].encode()}))
        rows = []
        for row in case["visibleEvents"]:
            number = int(row["sequence"])
            if query.after is not None and number <= query.after or query.before is not None and number >= query.before:
                continue
            if query.query and not _matches_query(row, query.query, True):
                continue
            rows.append(row)
        rows = rows[:query.limit + 1] if query.direction == "forward" else rows[-query.limit - 1:]
        assert event_page(query, rows, status, store, project_event) == example["response"]
    for example in case["errors"]:
        expected = example["response"]
        actual = error_response(example["statusCode"], expected["resetCursor"])
        assert json.loads(actual.body) == expected
    for value in golden["validation"]["invalidQueries"]:
        with pytest.raises(ValueError):
            EventQuery.parse(SimpleNamespace(scope={"query_string": value.encode()}))
    for value in golden["validation"]["validQueries"]:
        EventQuery.parse(SimpleNamespace(scope={"query_string": value.encode()}))


def test_actual_http_responses_validate_against_shared_schema(journal):
    """Validates real status/page/error responses against the portable HTTP schema when available."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(Path("contracts/agent-activity/http-v1/schema.json").read_text())
    persist(journal, [event("shared schema")])
    with client_for(journal) as client:
        for route in ("/status", "/events", "/events?limit=01", "/events?journalEpoch=old&afterSequence=0"):
            response = client.get(BASE + route)
            jsonschema.validate(response.json(), schema)
