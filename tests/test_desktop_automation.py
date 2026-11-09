"""Exercise the MCP window bridge, lifecycle fences and binary result boundary."""

import asyncio
import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent

from atlas.desktop_automation import DesktopAutomation, register_desktop_automation
from atlas.desktop_automation_mcp import register_desktop_tools

HEADERS = {"X-Atlas-MCP-Session": "a" * 32}
PREFIX = "/api/desktop-automation/"
STATE = {"projectId": "calc", "snapshotId": "published", "focus": {"target": "activity-launcher"}}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==")


def test_commands_and_capture_cross_the_real_mcp_and_http_entry_points():
    """Round-trip real tool registration and reject stale snapshot and wrong window."""
    bridge = DesktopAutomation()
    app = FastAPI()
    register_desktop_automation(app, bridge)
    client = TestClient(app)
    calls = []

    def host(operation, payload):
        calls.append((operation, payload))
        if operation == "inspect":
            return STATE
        if operation == "capture":
            return {"status": "ok", "pngBase64": base64.b64encode(PNG).decode()}
        return {"status": "ok", "state": STATE}

    bridge.attach(host)

    async def request(method, path, body, timeout=30):
        response = client.request(method, path, json=body, headers=HEADERS)
        response.raise_for_status()
        return response.json()

    async def scenario():
        mcp = FastMCP("Desktop")
        register_desktop_tools(mcp, request, lambda value: value or "calc", "calc")
        manager = mcp._tool_manager
        inspect = manager.get_tool("inspect_desktop_window").fn
        execute = manager.get_tool("execute_desktop_command").fn
        window = await inspect()
        assert window["state"] == STATE
        args = dict(windowId=window["windowId"], projectId="calc", snapshotId="published")
        result = await execute(**args, action="close_history")
        assert result["state"]["focus"]["target"] == "activity-launcher"
        images = await mcp.call_tool("capture_desktop_window", args)
        assert isinstance(images[0], ImageContent)
        assert base64.b64decode(images[0].data) == PNG
        stale = client.post(PREFIX + "execute", headers=HEADERS,
                            json={**args, "snapshotId": "old", "action": "open_history"})
        assert stale.status_code == 409
        assert [op for op, _ in calls if op != "inspect"] == ["execute", "capture"]
        bridge.detach(host)
        assert client.post(PREFIX + "inspect", headers=HEADERS, json={}).status_code == 503
        bridge.attach(host)
        assert client.post(PREFIX + "capture", headers=HEADERS, json=args).status_code == 409

    asyncio.run(scenario())


def test_opt_in_and_browser_boundary():
    """An ordinary launch and same-origin page requests cannot drive the window."""
    app = FastAPI()
    register_desktop_automation(app, None)
    client = TestClient(app)
    assert client.post(PREFIX + "inspect", json={}).status_code == 403
    assert client.post(PREFIX + "inspect", headers=HEADERS, json={}).status_code == 503
    browser = {**HEADERS, "Sec-Fetch-Site": "same-origin"}
    assert client.post(PREFIX + "inspect", headers=browser, json={}).status_code == 403


def test_capture_rejects_roundtrip_map_revision_change():
    """A project/snapshot match alone cannot validate pixels after A→B→A."""
    bridge = DesktopAutomation()
    app = FastAPI()
    register_desktop_automation(app, bridge)
    client = TestClient(app)
    generation = 1

    def host(operation, payload):
        nonlocal generation
        if operation == "inspect":
            return {**STATE, "revision": {"document": "document", "map": generation}}
        assert payload["revision"]["map"] == generation
        generation += 2
        return {"status": "ok", "pngBase64": base64.b64encode(PNG).decode()}

    bridge.attach(host)
    window_id = client.post(PREFIX + "inspect", headers=HEADERS, json={}).json()["windowId"]
    capture = client.post(PREFIX + "capture", headers=HEADERS, json={
        "windowId": window_id, "projectId": STATE["projectId"], "snapshotId": STATE["snapshotId"]})
    assert capture.status_code == 409
    assert "pngBase64" not in capture.json()
