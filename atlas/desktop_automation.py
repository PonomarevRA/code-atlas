"""Opt-in, bounded bridge from local MCP to one owned Atlas desktop window."""

import base64
from binascii import Error as Base64Error
import json
import re
from threading import Lock
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

MAX_PNG_BYTES = 4 * 1024 * 1024
ACTIONS = Literal["open_project", "open_node", "open_history", "filter_history",
                  "close_history", "return_to_map"]


class WindowScope(BaseModel):
    """An optional read scope and a mandatory identity for actions."""

    model_config = ConfigDict(extra="forbid")
    windowId: str | None = Field(default=None, max_length=32)
    projectId: str | None = Field(default=None, min_length=1, max_length=128)
    snapshotId: str | None = Field(default=None, min_length=1, max_length=128)


class WindowCommand(WindowScope):
    """Only navigation and read-only journal operations cross the bridge."""

    windowId: str = Field(min_length=32, max_length=32)
    action: ACTIONS
    nodeId: str | None = Field(default=None, min_length=1, max_length=512)
    query: str | None = Field(default=None, max_length=512)


class DesktopAutomation:
    """Bind commands to the active window and serialize calls without a work queue."""

    def __init__(self):
        self.binding = None
        self.lifecycle = Lock()
        self.operation = Lock()

    def attach(self, handler):
        """Create a new identity for each window lifetime."""
        with self.lifecycle:
            self.binding = (uuid4().hex, handler)

    def detach(self, handler):
        """Retire only the handler being closed."""
        with self.lifecycle:
            if self.binding and self.binding[1] is handler:
                self.binding = None

    def call(self, operation, scope):
        """Reject stale identities and scopes before invoking a native adapter."""
        if not self.operation.acquire(blocking=False):
            raise HTTPException(409, "desktop_window_busy")
        try:
            return self._call_bound(operation, scope)
        finally:
            self.operation.release()

    def _call_bound(self, operation, scope):
        """Fence native results against window closure and bound response size."""
        with self.lifecycle:
            binding = self.binding
        if binding is None:
            raise HTTPException(503, "desktop_window_unavailable")
        window_id, handler = binding
        if scope.windowId is not None and scope.windowId != window_id:
            raise HTTPException(409, "desktop_window_changed")

        def invoke(name, payload):
            try:
                result = handler(name, payload)
                if not isinstance(result, dict):
                    raise TypeError("Expected object")
                limit = 4 * ((MAX_PNG_BYTES + 2) // 3) + 4096 if name == "capture" else 32_768
                if len(json.dumps(result, ensure_ascii=False).encode()) > limit:
                    raise HTTPException(413, "desktop_result_too_large")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, "desktop_host_unavailable") from None
            with self.lifecycle:
                if self.binding is not binding:
                    raise HTTPException(409, "desktop_window_changed")
            return result

        state = invoke("inspect", {})
        if state.get("status") == "error":
            return {**state, "windowId": window_id}
        switching_project = operation == "execute" and scope.action == "open_project"
        if not switching_project:
            self._assert_scope(state, scope)
        if operation == "inspect":
            return {"status": "ok", "windowId": window_id, "state": state}
        payload = scope.model_dump(exclude={"windowId"}, exclude_none=True)
        if operation == "capture":
            payload["revision"] = state.get("revision")
        result = invoke(operation, payload)
        if operation == "execute":
            observed = result.get("state")
            if result.get("status") == "ok":
                if not isinstance(observed, dict):
                    raise HTTPException(502, "desktop_result_missing_state")
                self._assert_scope(observed, scope)
            elif observed is not None:
                result = {key: value for key, value in result.items() if key != "state"}
        if operation == "capture" and result.get("status") == "ok":
            self._validate_png(result.get("pngBase64"))
            current = invoke("inspect", {})
            self._assert_scope(current, scope)
            if current.get("revision") != state.get("revision"):
                raise HTTPException(409, "desktop_window_changed")
        return {**result, "windowId": window_id}

    @staticmethod
    def _assert_scope(state, scope):
        """Require every supplied project and snapshot fence to match."""
        for field in ("projectId", "snapshotId"):
            expected = getattr(scope, field)
            if expected is not None and state.get(field) != expected:
                raise HTTPException(409, "desktop_scope_changed")

    @staticmethod
    def _validate_png(encoded):
        """Reject invalid and oversized image payloads before MCP forwarding."""
        try:
            png = base64.b64decode(encoded, validate=True)
        except (Base64Error, ValueError, TypeError):
            raise HTTPException(502, "desktop_invalid_png") from None
        if len(png) > MAX_PNG_BYTES:
            raise HTTPException(413, "desktop_result_too_large")
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(502, "desktop_invalid_png")


def register_desktop_automation(app, automation):
    """Expose opt-in control to local processes and reject browser requests."""
    def authorize(request: Request):
        """Require the transport header for request routing, with no authentication claim."""
        if ("origin" in request.headers or "sec-fetch-site" in request.headers or
                not re.fullmatch(r"[0-9a-f]{32}", request.headers.get("x-atlas-mcp-session", ""))):
            raise HTTPException(403, "desktop_mcp_transport_required")
        if automation is None:
            raise HTTPException(503, "desktop_automation_disabled")

    @app.post("/api/desktop-automation/inspect")
    def inspect(request: Request, scope: WindowScope):
        authorize(request)
        return automation.call("inspect", scope)

    @app.post("/api/desktop-automation/execute")
    def execute(request: Request, command: WindowCommand):
        authorize(request)
        if not command.projectId or not command.snapshotId:
            raise HTTPException(422, "desktop_snapshot_required")
        if command.action == "open_node" and not command.nodeId:
            raise HTTPException(422, "desktop_node_required")
        if command.action == "filter_history" and command.query is None:
            raise HTTPException(422, "desktop_query_required")
        return automation.call("execute", command)

    @app.post("/api/desktop-automation/capture")
    def capture(request: Request, scope: WindowScope):
        authorize(request)
        if not (scope.windowId and scope.projectId and scope.snapshotId):
            raise HTTPException(422, "desktop_snapshot_required")
        return automation.call("capture", scope)
