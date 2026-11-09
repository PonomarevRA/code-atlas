"""Contract checks for bounded MCP responses and reusable isolated HTTP connections."""

import asyncio
from copy import deepcopy
import json
import random

import httpx
from mcp.server.fastmcp import FastMCP
import pytest

from atlas.code_health_mcp import bounded_health, register_health_tools
from atlas.mcp_response_budget import bound_list_response, compact_description, json_bytes
from atlas.mcp_transport import LoopbackTransport


def tool_data(result):
    """Read FastMCP's structured or text contract in direct tool tests."""
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple):
        return result[1]
    return json.loads(result[0].text)


def test_exact_budget_prefix_matches_reference_and_preserves_input():
    """Unicode, escapes, separators and multiple list priorities retain the exact legacy prefix."""
    rng = random.Random(721)
    for _ in range(80):
        result = {"snapshotId": "s", "nodes": [{"id": i, "text": rng.choice(["я", "🙂", '"\\\n']) * rng.randrange(200)} for i in range(rng.randrange(40))],
                  "findings": ["ж" * rng.randrange(100) for _ in range(10)]}
        original = deepcopy(result)
        budget = rng.randrange(80, 3000)
        expected = deepcopy(result)
        for key in ("nodes", "findings"):
            while expected[key] and json_bytes(expected) > budget:
                expected[key].pop()
                expected["truncated"] = True
        actual = bound_list_response(result, ("nodes", "findings"), budget=budget)
        if json_bytes(expected) > budget:
            assert actual is None
        else:
            assert actual == expected
            assert json_bytes(actual) <= budget
        assert result == original
    assert bound_list_response({"nodes": [], "metadata": "x" * 100}, ("nodes",), budget=20) is None


def test_health_compaction_does_not_mutate_immutable_record():
    """Response-only omission leaves saved lexical calls, contracts and the entire page intact."""
    source = {"assessmentId": "a", "nodes": [{"healthIdentity": str(i), "lexicalCalls": ["call"],
              "documentationHealth": {"contract": {"summary": "full"}, "status": "complete"}, "details": "я" * 4000} for i in range(30)]}
    previous = deepcopy(source)
    result = bounded_health(source)
    assert source == previous
    assert 0 < len(result["nodes"]) < 30 and result["responseTruncated"]
    assert "lexicalCalls" not in result["nodes"][0]
    assert "contract" not in result["nodes"][0]["documentationHealth"]
    assert json_bytes(result) <= 64_000


def test_health_report_can_traverse_fat_rows_without_skips():
    """An MCP-truncated final API page must also expose a continuation for its omitted rows."""
    rows = [{"id": str(i), "healthIdentity": str(i), "details": "я" * 6000} for i in range(23)]
    sources = []

    async def request(method, path):
        """Return real API cursor semantics with a small deterministic corpus."""
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(path).query)
        cursor, limit = int(query["cursor"][0]), int(query["limit"][0])
        source = {"nodes": rows[cursor:cursor + limit], "nextCursor": cursor + limit if cursor + limit < len(rows) else None,
                  "totalMethods": len(rows), "truncated": cursor + limit < len(rows), "snapshotId": "fixed"}
        sources.append(deepcopy(source))
        return source

    async def scenario():
        """Visit every source row once using only returned nextCursor values."""
        server = FastMCP("pagination")
        register_health_tools(server, request, lambda project: project)
        seen, cursor = [], 0
        for _ in range(30):
            result = tool_data(await server.call_tool("get_code_health_report", {"projectId": "one", "limit": 12, "cursor": cursor}))
            assert json_bytes(result) <= 64_000
            seen.extend(node["id"] for node in result["nodes"])
            cursor = result["nextCursor"]
            if cursor is None:
                break
        assert seen == [row["id"] for row in rows]
        assert len(sources[0]["nodes"]) == 12
    asyncio.run(scenario())


def test_health_report_reports_unreadable_row_instead_of_a_nonadvancing_cursor():
    """An individually oversized row gives a recoverable workflow rather than an infinite page loop."""
    async def request(method, path):
        return {"nodes": [{"id": "large", "details": "x" * 70_000}], "nextCursor": None}

    async def scenario():
        server = FastMCP("oversize")
        register_health_tools(server, request, lambda project: project)
        with pytest.raises(Exception, match="get_code_health_record"):
            await server.call_tool("get_code_health_report", {"projectId": "one"})
    asyncio.run(scenario())


def test_description_fallback_preserves_health_provenance_with_bounded_details():
    """Oversized comments and findings do not erase measured status or overflow the fallback."""
    result = {"factualSummary": "я" * 20000, "health": {"score": 41, "status": "partial", "version": "v1",
              "components": {"structure": 60}, "dependencies": {"status": "partial", "unknownTargets": 3},
              "findings": ["x" * 1000] * 500}, "sourceKind": "working-tree",
              "sourceProvenance": {"status": "changed", "sourceHash": "hash"}, "comment": "ж" * 100000}
    previous = deepcopy(result)
    actual = compact_description(result, "s", "n", ("comment",))
    assert actual["health"]["score"] == 41 and actual["health"]["status"] == "partial"
    assert actual["health"]["dependencies"]["unknownTargets"] == 3
    assert actual["health"]["detailsOmitted"] is True
    assert actual["sourceProvenance"] == result["sourceProvenance"]
    assert actual["sourceKind"] == "working-tree" and json_bytes(actual) <= 64_000
    assert result == previous


def test_description_fallback_has_one_total_budget_across_many_sections():
    """Individually acceptable Unicode fields cannot combine into an oversized response."""
    result = {key: "🙂" * 1400 for key in ("sourceKind", "file", "sourceHash", "analyzerVersion", "comment", "implementationComment")}
    result.update(factualSummary="🙂" * 4000,
                  health={"score": 19, "status": "partial", "findings": ["я" * 2500]},
                  complexity={"status": "partial", "detail": "я" * 2500},
                  sourceProvenance={"status": "changed", "detail": "я" * 2500},
                  readabilityRecommendations={"detail": "я" * 2500})
    actual = compact_description(result, "s", "n", ("comment", "implementationComment"))
    assert actual["health"]["score"] == 19
    assert actual["sourceProvenance"]["status"] == "changed"
    assert json_bytes(actual) <= 64_000 and actual["detailsOmitted"]


def test_large_description_keeps_common_language_and_unsupported_flow_boundary():
    """A response-size fallback must not turn an incomplete language flow into unqualified facts."""
    original = {'sourceStatus': 'parsed', 'language': 'python',
                'flowProvider': {'id': 'python', 'contractVersion': 'atlas-method-flow-v1'},
                'methodFlow': {'status': 'partial', 'truncated': False, 'scope': 'local method body',
                               'contractVersion': 'atlas-method-flow-v1',
                               'unsupported': ['with/cleanup is partial'],
                               'steps': [{'text': 'x' * 80_000}]}}
    actual = compact_description(original, 'snapshot', 'raw-id', ())
    assert actual['language'] == 'python' and actual['sourceStatus'] == 'parsed'
    assert actual['flowProvider'] == original['flowProvider']
    assert actual['methodFlow']['unsupported'] == ['with/cleanup is partial']
    assert actual['methodFlow']['status'] == 'partial'
    assert actual['methodFlow']['truncated'] is False  # Transport omission is a separate boundary.
    assert actual['methodFlow']['stepsOmitted'] is True and 'steps' not in actual['methodFlow']
    assert original['methodFlow']['steps'] and json_bytes(actual) <= 64_000


def test_transport_lifespan_reuses_client_and_closes_after_concurrent_requests(monkeypatch):
    """One bounded pool serves concurrent calls, preserves timeouts and closes on lifespan exit."""
    clients, requests = [], []
    original = httpx.AsyncClient

    async def handler(request):
        """Yield once so concurrent requests overlap on the same connection pool."""
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"ok": True})

    def client(**options):
        """Assert isolation and retain real httpx lifecycle state."""
        assert options["trust_env"] is False and options["follow_redirects"] is False
        assert options["limits"].max_connections == 16
        created = original(**options, transport=httpx.MockTransport(handler))
        clients.append(created)
        return created

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def scenario():
        """Exercise managed and standalone lifecycle paths without leaking an AsyncClient."""
        transport = LoopbackTransport(8877)
        async with transport.lifespan(None):
            results = await asyncio.gather(*(transport.request("GET", "/api/health", timeout=i + 1) for i in range(5)))
            assert all(row == {"ok": True} for row in results)
            assert len(clients) == 1 and not clients[0].is_closed
        assert clients[0].is_closed and transport.client is None
        assert [request.extensions["timeout"]["read"] for request in requests] == [1, 2, 3, 4, 5]
        assert await transport.request("GET", "/api/health") == {"ok": True}
        assert len(clients) == 2 and clients[1].is_closed
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["redirect", "oversize", "error", "timeout"])
def test_transport_rejects_redirects_oversized_stream_and_preserves_error_contract(monkeypatch, mode):
    """Bounds apply before following redirects or collecting an oversized streaming body."""
    from atlas import mcp_transport
    original = httpx.AsyncClient
    read_chunks = []

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            """Expose whether the reader stops before exhausting the source response."""
            for number in range(10):
                read_chunks.append(number)
                yield b"x" * 64 * 1024

    def handler(request):
        """Return each boundary condition from the same fixed origin."""
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.org/secret"})
        if mode == "oversize":
            return httpx.Response(200, stream=Chunks())
        if mode == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return httpx.Response(409, json={"error": "stale snapshot"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: original(**options, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(mcp_transport, "MAX_HTTP_RESPONSE_BYTES", 70_000)
    expected = {"redirect": "Перенаправления", "oversize": "превышает", "error": "HTTP 409.*stale snapshot", "timeout": "не успел"}
    with pytest.raises(ValueError, match=expected[mode]):
        asyncio.run(LoopbackTransport(8877).request("GET", "/fixture"))
    if mode == "oversize":
        assert read_chunks == [0, 1]


@pytest.mark.parametrize("response, expected", [
    (httpx.Response(422, json={"detail": [{"loc": ["body", "limit"], "msg": "Input should be less than or equal to 120", "input": "private source"}]}), "HTTP 422.*body.limit: Input should"),
    (httpx.Response(200, text='<html>different application</html>'), "некорректный JSON"),
    (httpx.Response(200, json=[]), "неожиданный формат"),
    (httpx.Response(500, json=["private source"]), "HTTP 500.*проверьте снимок"),
])
def test_transport_exposes_actionable_validation_and_protocol_errors(monkeypatch, response, expected):
    """Agents see the failing field or protocol remedy without leaking rejected input or HTML."""
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **options: original(
        **options, transport=httpx.MockTransport(lambda request: response)))
    with pytest.raises(ValueError, match=expected) as failure:
        asyncio.run(LoopbackTransport(8877).request('GET', '/fixture'))
    assert 'private source' not in str(failure.value) and '<html>' not in str(failure.value)
