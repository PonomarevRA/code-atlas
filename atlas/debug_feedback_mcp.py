"""Source-bound imported failure context through the existing MCP loopback proxy."""

from typing import Annotated
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field

from .mcp_response_budget import json_bytes


DEBUG_FEEDBACK_TOOLS = ["prepare_test_feedback"]
IDENTITY = Annotated[str, Field(min_length=1, max_length=128)]


def register_debug_feedback_tools(server, request, selected_project) -> None:
    """Register a read-only preparation tool; imported XML is not executable authority."""
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def prepare_test_feedback(
        snapshotId: IDENTITY, reportId: IDENTITY,
        junitXml: Annotated[str, Field(min_length=1, max_length=65536)],
        projectId: str | None = None,
        reportedSnapshotId: IDENTITY | None = None,
        reportedBuildId: IDENTITY | None = None,
    ) -> dict:
        """
        Prepare bounded test failures and static source candidates from JUnit XML.

        UTF-8 XML only; DTD/entities are rejected. Report snapshot/build values
        are caller declarations, not verified CI provenance. Missing results
        never mean passing tests. No project tests, model or source code execute.
        """
        project_id = selected_project(projectId)
        body = {
            "snapshotId": snapshotId, "reportId": reportId, "junitXml": junitXml,
            "reportedSnapshotId": reportedSnapshotId, "reportedBuildId": reportedBuildId,
        }
        result = await request("POST", "/api/projects/" + quote(project_id, safe="") + "/debug-feedback", body)
        if json_bytes(result) > 64_000:
            raise ValueError("Feedback exceeds MCP budget; provide a smaller JUnit report.")
        return result
