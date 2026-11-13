"""MCP tools for an explicitly enabled local Atlas desktop window."""

import base64

from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from .desktop_automation import ACTIONS

# The broker may inspect before and after a bounded native operation.
_WINDOW_REQUEST_TIMEOUT = 70
DESKTOP_TOOLS = ('inspect_desktop_window', 'execute_desktop_command', 'capture_desktop_window')


def register_desktop_tools(server, request, selected_project, bound_project):
    """Preserve a project-bound MCP client's existing scope across UI operations."""

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def inspect_desktop_window(projectId: str | None = None) -> dict:
        """Read the owned window identity, current map, panel, focus and layout."""
        scope = {"projectId": selected_project(projectId)} if projectId or bound_project else {}
        return await request("POST", "/api/desktop-automation/inspect", scope)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    async def execute_desktop_command(windowId: str, projectId: str, snapshotId: str,
                                      action: ACTIONS, nodeId: str | None = None,
                                      query: str | None = None) -> dict:
        """Run an allowed UI command for a specific window, project and snapshot."""
        payload = dict(windowId=windowId, projectId=selected_project(projectId),
                       snapshotId=snapshotId, action=action)
        if nodeId is not None:
            payload["nodeId"] = nodeId
        if query is not None:
            payload["query"] = query
        return await request("POST", "/api/desktop-automation/execute", payload,
                             timeout=_WINDOW_REQUEST_TIMEOUT)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def capture_desktop_window(windowId: str, projectId: str, snapshotId: str):
        """Capture Atlas WebView content as PNG, where the native renderer supports it."""
        result = await request("POST", "/api/desktop-automation/capture", {
            "windowId": windowId, "projectId": selected_project(projectId), "snapshotId": snapshotId},
            timeout=_WINDOW_REQUEST_TIMEOUT)
        if result.get("status") != "ok":
            return result
        return Image(data=base64.b64decode(result["pngBase64"], validate=True), format="png")
