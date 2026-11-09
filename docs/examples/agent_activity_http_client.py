"""Read Atlas or AgMemory activity using the same stdlib-only HTTP client.

    python docs/examples/agent_activity_http_client.py http://127.0.0.1:8765 --pages 2

Only the base URL changes between services. Reading never clears the journal,
calls a model or resets a cursor automatically. Backward pages contain events
in ascending order within each page; pages themselves travel toward older data.
"""

import argparse
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SCHEMA = "agent-activity-http/v1"
MAX_BYTES = 262144
MAX_SEQUENCE = 9223372036854775807
FILTERS = {"sessionId", "operation", "q", "projectId"}


class ActivityApiError(RuntimeError):
    """Expose HTTP status, safe code and optional 409 reset cursor without resetting."""

    def __init__(self, status, code="http_error", retryable=False, reset_cursor=None):
        """Store a safe HTTP failure; callers explicitly decide whether to resume or retry."""
        self.status = status
        self.code = code
        self.retryable = retryable
        self.reset_cursor = reset_cursor
        super().__init__(f"Activity API HTTP {status}: {code}")


class ActivityProtocolError(RuntimeError):
    """A successful response violates the bounded JSON/pagination contract."""


class _NoRedirect(HTTPRedirectHandler):
    """Keep the request on its configured service origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Decline redirects so a local service cannot move reads to another origin."""
        return None


def _sequence(value):
    """Validate a canonical decimal string without converting through floating point."""
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
        raise ActivityProtocolError("Invalid decimal sequence")
    if int(value) > MAX_SEQUENCE:
        raise ActivityProtocolError("Sequence exceeds the v1 range")
    return value


def _cursor(value):
    """Copy a validated exclusive cursor; no caller-owned dictionary is retained."""
    if not isinstance(value, dict):
        raise ActivityProtocolError("Missing cursor")
    epoch = value.get("journalEpoch")
    if not isinstance(epoch, str) or not 1 <= len(epoch) <= 128:
        raise ActivityProtocolError("Invalid journal epoch")
    try:
        epoch.encode("utf-8")
    except UnicodeError as error:
        raise ActivityProtocolError("Invalid Unicode epoch") from error
    return {"journalEpoch": epoch, "sequence": _sequence(value.get("sequence"))}


class ActivityHttpClient:
    """Read one loopback service; application names never select different behavior."""

    def __init__(self, base_url, timeout=5.0):
        """Require a loopback origin and isolate requests from ambient proxy configuration."""
        origin = urlsplit(base_url)
        if (origin.scheme not in {"http", "https"} or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
                or origin.username is not None or origin.password is not None
                or origin.path not in {"", "/"} or origin.query or origin.fragment):
            raise ValueError("Use a loopback HTTP(S) service origin")
        origin.port  # Reject malformed ports before issuing a request.
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _get(self, route, parameters=None):
        """Issue one GET, bound bytes and honor errors even when a host returns non-JSON."""
        url = self.base_url + "/api/agent-activity/v1/" + route
        if parameters:
            url += "?" + urlencode(parameters, encoding="utf-8", errors="strict")
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            # Error bodies from Host/Origin rejection are not necessarily API envelopes.
            with error:
                try:
                    body = self._decode(error)
                except (ActivityProtocolError, OSError):
                    body = {}
            detail = body.get("error") if body.get("schemaVersion") == SCHEMA else None
            code = detail.get("code") if isinstance(detail, dict) else "http_error"
            allowed = {"invalid_query", "forbidden", "cursor_expired", "journal_unavailable"}
            if not isinstance(code, str) or code not in allowed:
                code = "http_error"
            reset = None
            if error.code == 409 and body.get("resetCursor") is not None:
                try:
                    reset = _cursor(body["resetCursor"])
                except ActivityProtocolError:
                    # Preserve the known HTTP failure even when a host's error body is malformed.
                    pass
            raise ActivityApiError(error.code, code, error.code == 503, reset) from None
        except (URLError, TimeoutError, OSError) as error:
            raise ActivityProtocolError("Activity HTTP transport unavailable") from error
        with response:
            body = self._decode(response)
        if body.get("schemaVersion") != SCHEMA:
            raise ActivityProtocolError("Unsupported activity HTTP schema")
        return body

    @staticmethod
    def _decode(response):
        """Decode at most one page of UTF-8 JSON; raw HTML/error payloads are never echoed."""
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ActivityProtocolError("Activity response exceeds 262144 bytes")
        if response.headers.get_content_type() != "application/json":
            raise ActivityProtocolError("Activity response is not JSON")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as error:
            raise ActivityProtocolError("Invalid UTF-8 JSON response") from error
        if not isinstance(body, dict):
            raise ActivityProtocolError("Expected a JSON object")
        return body

    def status(self):
        """Read durable bounds and normalized health without any subscription or write."""
        body = self._get("status")
        _cursor(body.get("cursor"))
        return body

    def events(self, *, after=None, before=None, limit=100, **filters):
        """Read one page; after/before are full cursors and exact filters share both services."""
        if after is not None and before is not None:
            raise ValueError("after and before are mutually exclusive")
        if type(limit) is not int or not 1 <= limit <= 100 or set(filters) - FILTERS:
            raise ValueError("Use limit 1..100 and supported filters")
        parameters = {"limit": str(limit), **filters}
        boundary = after if after is not None else before
        direction = "forward" if after is not None else "backward"
        if boundary is not None:
            boundary = _cursor(boundary)
            parameters["journalEpoch"] = boundary["journalEpoch"]
            parameters["afterSequence" if direction == "forward" else "beforeSequence"] = boundary["sequence"]
        body = self._get("events", parameters)
        next_cursor = _cursor(body.get("nextCursor"))
        head = _cursor(body.get("highWatermark"))
        rows = body.get("events")
        if body.get("direction") != direction or not isinstance(rows, list) or len(rows) > limit:
            raise ActivityProtocolError("Invalid page direction or event count")
        if next_cursor["journalEpoch"] != head["journalEpoch"]:
            raise ActivityProtocolError("Page cursors have different epochs")
        if boundary is not None and boundary["journalEpoch"] != head["journalEpoch"]:
            raise ActivityProtocolError("Epoch changed without HTTP 409")
        previous = -1
        for row in rows:
            if not isinstance(row, dict):
                raise ActivityProtocolError("Invalid event object")
            sequence = int(_sequence(row.get("sequence")))
            if sequence <= previous or sequence > int(head["sequence"]) or row.get("journalEpoch") != head["journalEpoch"]:
                raise ActivityProtocolError("Events are not an ascending committed page")
            if boundary is not None:
                outside = sequence <= int(boundary["sequence"]) if direction == "forward" else sequence >= int(boundary["sequence"])
                if outside:
                    raise ActivityProtocolError("Event crosses its exclusive boundary")
            previous = sequence
        if type(body.get("hasMore")) is not bool or type(body.get("truncated")) is not bool:
            raise ActivityProtocolError("Missing pagination flags")
        if body["hasMore"] and not rows or body["truncated"] and not body["hasMore"]:
            raise ActivityProtocolError("Invalid pagination boundary")
        if body["hasMore"]:
            # Durable head can exceed the page edge; following it would skip matching events.
            edge = rows[-1] if direction == "forward" else rows[0]
            if not isinstance(edge, dict) or edge.get("sequence") != next_cursor["sequence"]:
                raise ActivityProtocolError("Continuation does not match the included page edge")
        if direction == "forward" and not body["hasMore"] and next_cursor != head:
            raise ActivityProtocolError("Finished forward page must advance to its head")
        if direction == "backward" and not body["hasMore"]:
            oldest = int(_sequence(body.get("oldestSequence")))
            if oldest < 1 or next_cursor["sequence"] != str(oldest - 1):
                raise ActivityProtocolError("Finished backward page must reach the retained boundary")
        return body

    def pages(self, *, direction="backward", cursor=None, limit=100, **filters):
        """Yield pages until hasMore=false; explicit cursors and 409 failures remain visible."""
        if direction not in {"forward", "backward"} or direction == "forward" and cursor is None:
            raise ValueError("Forward traversal requires an explicit cursor")
        boundary = _cursor(cursor) if cursor is not None else None
        while True:
            arguments = {"after" if direction == "forward" else "before": boundary}
            page = self.events(limit=limit, **arguments, **filters)
            next_cursor = _cursor(page["nextCursor"])
            if page["hasMore"] and boundary is not None:
                advanced = int(next_cursor["sequence"]) - int(boundary["sequence"])
                if next_cursor["journalEpoch"] != boundary["journalEpoch"] or (advanced <= 0 if direction == "forward" else advanced >= 0):
                    raise ActivityProtocolError("Continuation made no progress")
            yield page
            if not page["hasMore"]:
                return
            boundary = next_cursor


def main(argv=None):
    """Print status and a bounded number of read-only pages; errors exit without auto-reset."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--limit", type=int, default=100)
    options = parser.parse_args(argv)
    if options.pages < 1:
        parser.error("--pages must be positive")
    try:
        client = ActivityHttpClient(options.base_url)
        print(json.dumps(client.status(), ensure_ascii=False))
        for index, page in enumerate(client.pages(limit=options.limit), 1):
            print(json.dumps(page, ensure_ascii=False))
            if index >= options.pages:
                break
    except (ActivityApiError, ActivityProtocolError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

