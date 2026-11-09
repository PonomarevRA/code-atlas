"""One read-only client consumes both services' shared fixtures and cursor/error semantics."""

from copy import deepcopy
from email.message import Message
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("activity_http_example", ROOT / "docs/examples/agent_activity_http_client.py")
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)
CASES = json.loads((ROOT / "contracts/agent-activity/http-v1/examples.json").read_text())["cases"]


class Response(BytesIO):
    """Represent a small urllib response without opening sockets or writing journal data."""

    def __init__(self, body, content_type="application/json"):
        """Serialize fixture envelopes as actual UTF-8 bytes for response-size checks."""
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        super().__init__(raw)
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class Opener:
    """Record exact GET URLs and return queued wire responses/errors once each."""

    def __init__(self, *responses):
        """Queue deterministic wire results and retain requests for read-only assertions."""
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        """Assert GET with no body before consuming exactly one scripted response."""
        self.requests.append(request)
        assert request.get_method() == "GET"
        assert request.data is None
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def client(*responses):
    """Inject only HTTP transport; the application-specific API behavior stays untouched."""
    value = CLIENT.ActivityHttpClient("http://127.0.0.1:19999")
    value._opener = Opener(*responses)
    return value


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["application"])
def test_same_client_reads_status_and_backward_pages_from_both_services(case):
    """Backward continuation uses earliest event, preserving ascending order inside each page."""
    value = client(Response(case["status"]), *(Response(item["response"]) for item in case["pages"][:2]))
    assert value.status() == case["status"]
    pages = list(value.pages(limit=2))
    assert [[event["sequence"] for event in page["events"]] for page in pages] == [["5", "8"], ["3"]]
    request = value._opener.requests[-1]
    assert parse_qs(urlsplit(request.full_url).query) == {"limit": ["2"], "journalEpoch": ["http-example-epoch"], "beforeSequence": ["5"]}


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["application"])
def test_forward_pages_distinguish_next_cursor_from_head_and_empty_filter(case):
    """Head=10 must not skip matching event8 when page1 continues at5; empty tail advances head."""
    value = client(*(Response(item["response"]) for item in case["pages"][2:]))
    start = {"journalEpoch": "http-example-epoch", "sequence": "2"}
    pages = list(value.pages(direction="forward", cursor=start, limit=2))
    assert [event["sequence"] for page in pages for event in page["events"]] == ["3", "5", "8"]
    assert parse_qs(urlsplit(value._opener.requests[1].full_url).query)["afterSequence"] == ["5"]
    empty = value.events(after=start, q="unmatched")
    assert empty["events"] == []
    assert empty["nextCursor"] == empty["highWatermark"]


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["application"])
@pytest.mark.parametrize("status", [400, 403, 409, 503])
def test_api_errors_remain_explicit_and_never_auto_reset_or_retry(case, status):
    """A 409 exposes resetCursor once; caller must choose whether to discard its former position."""
    body = next(item["response"] for item in case["errors"] if item["statusCode"] == status)
    response = Response(body)
    error = HTTPError("http://127.0.0.1:19999/", status, "Error", response.headers, response)
    value = client(error)
    with pytest.raises(CLIENT.ActivityApiError) as caught:
        value.events(after={"journalEpoch": "http-example-epoch", "sequence": "0"})
    assert caught.value.status == status
    assert caught.value.code == body["error"]["code"]
    assert caught.value.reset_cursor == body["resetCursor"]
    assert caught.value.retryable is (status == 503)
    assert len(value._opener.requests) == 1


def test_non_json_host_forbidden_retains_status_without_echoing_payload():
    """Transport guards can reject before API envelopes; their HTML must not become error text."""
    response = Response(b"private /local/storage/path", "text/html")
    value = client(HTTPError("http://127.0.0.1:19999/", 403, "Forbidden", response.headers, response))
    with pytest.raises(CLIENT.ActivityApiError) as caught:
        value.status()
    assert caught.value.status == 403
    assert "private" not in str(caught.value)
    assert "/local/storage" not in str(caught.value)


def test_malformed_reset_body_still_preserves_http_409():
    """Known HTTP failures remain failures even if their optional JSON cursor is corrupt."""
    body = {"schemaVersion": CLIENT.SCHEMA, "error": {"code": "cursor_expired"}, "resetCursor": {"sequence": 1}}
    response = Response(body)
    value = client(HTTPError("http://127.0.0.1:19999/", 409, "Conflict", response.headers, response))
    with pytest.raises(CLIENT.ActivityApiError) as caught:
        value.events()
    assert caught.value.status == 409
    assert caught.value.reset_cursor is None


def test_page_bytes_are_bounded_before_json_and_schema_or_order_errors_are_rejected():
    """UTF-8 byte budget and ascending ordering are independent of Python character count."""
    value = client(Response(("Ж" * 131073).encode("utf-8")))
    with pytest.raises(CLIENT.ActivityProtocolError, match="262144"):
        value.status()
    bad = deepcopy(CASES[0]["pages"][0]["response"])
    bad["events"].reverse()
    value = client(Response(bad))
    with pytest.raises(CLIENT.ActivityProtocolError, match="ascending"):
        value.events(limit=2)


def test_byte_truncation_uses_last_included_event_then_continues_without_skip():
    """Byte-limited partial forward pages continue at the included edge, never at durable head."""
    page = deepcopy(CASES[0]["pages"][2]["response"])
    page["events"] = page["events"][:1]
    page["nextCursor"]["sequence"] = "3"
    page["truncated"] = True
    second = deepcopy(CASES[0]["pages"][3]["response"])
    second["events"].insert(0, CASES[0]["visibleEvents"][1])
    value = client(Response(page), Response(second))
    rows = list(value.pages(direction="forward", cursor={"journalEpoch": "http-example-epoch", "sequence": "2"}, limit=2))
    assert [item["sequence"] for row in rows for item in row["events"]] == ["3", "5", "8"]
    assert parse_qs(urlsplit(value._opener.requests[1].full_url).query)["afterSequence"] == ["3"]


def test_input_cursors_are_not_mutated_and_invalid_combinations_never_send_http():
    """Exact filters travel as UTF-8 query data, while malformed local options fail before I/O."""
    start = {"journalEpoch": "http-example-epoch", "sequence": "2"}
    value = client(Response(CASES[0]["pages"][4]["response"]))
    value.events(after=start, q="ПИФ&x=1")
    assert start == {"journalEpoch": "http-example-epoch", "sequence": "2"}
    assert parse_qs(urlsplit(value._opener.requests[0].full_url).query)["q"] == ["ПИФ&x=1"]
    with pytest.raises(ValueError):
        value.events(after=start, before=start)
    with pytest.raises(ValueError):
        value.events(limit=True)
    with pytest.raises(ValueError):
        value.events(unknown="filter")
    assert len(value._opener.requests) == 1


def test_nonprogressing_or_wrong_head_continuation_is_not_silently_followed():
    """A faulty server cannot cause an infinite page loop or skip the forward tail."""
    broken = deepcopy(CASES[0]["pages"][2]["response"])
    broken["nextCursor"] = broken["highWatermark"]
    value = client(Response(broken))
    with pytest.raises(CLIENT.ActivityProtocolError, match="included page edge"):
        list(value.pages(direction="forward", cursor={"journalEpoch": "http-example-epoch", "sequence": "2"}, limit=2))


def read_only_service_report(base_url):
    """Exercise any real isolated service with the same client; derive filters from its permitted events."""
    value = CLIENT.ActivityHttpClient(base_url)
    status = value.status()
    start = {"journalEpoch": status["journalEpoch"], "sequence": str(int(status["oldestSequence"]) - 1)}
    forward = list(value.pages(direction="forward", cursor=start, limit=100))
    backward = list(value.pages(limit=2))
    forward_rows = [event for page in forward for event in page["events"]]
    backward_rows = [event for page in backward for event in page["events"]]
    assert len({event["eventId"] for event in forward_rows}) == len(forward_rows)
    assert {event["eventId"] for event in forward_rows} == {event["eventId"] for event in backward_rows}
    assert len(forward_rows) == len(backward_rows)
    assert value.status()["cursor"] == status["cursor"], "Fixture changed during read-only QA; retry on a stable synthetic service"
    assert value.events(after=start, q="not-present-qa-Ω")['nextCursor'] == status['cursor']
    if forward_rows:
        first = forward_rows[0]
        for field, expected in [("sessionId", first["sessionId"]), ("operation", first["operation"])]:
            rows = [event for page in value.pages(direction="forward", cursor=start, **{field: expected}) for event in page["events"]]
            assert [event["eventId"] for event in rows] == [event["eventId"] for event in forward_rows if event[field] == expected]
    for route, query, expected_status in [
        ("status", {"unexpected": "x"}, 400),
        ("events", {"limit": "01"}, 400),
        ("events?limit=2&limit=3", None, 400),
        ("events?q=%ED%A0%80", None, 400),
        ("events", {"journalEpoch": "expired-" + status["journalEpoch"], "afterSequence": "0"}, 409),
    ]:
        with pytest.raises(CLIENT.ActivityApiError) as caught:
            value._get(route, query)
        assert caught.value.status == expected_status
        if expected_status == 409:
            assert caught.value.reset_cursor == status["cursor"]
    return {"baseUrl": base_url, "application": status["application"], "head": status["latestSequence"],
            "forwardCount": len(forward_rows), "backwardCount": len(backward_rows),
            "forwardPageCounts": [len(page["events"]) for page in forward],
            "backwardPages": len(backward), "byteTruncatedPages": sum(page["truncated"] for page in forward), "writes": 0}


@pytest.mark.parametrize("base_url", [item for item in os.environ.get("AGENT_ACTIVITY_HTTP_BASE_URLS", "").split(",") if item])
def test_live_uniform_read_only_client_on_explicit_synthetic_services(base_url):
    """Opt-in QA changes only base URL; default unit tests never contact a running application."""
    report = read_only_service_report(base_url)
    print(json.dumps(report))
