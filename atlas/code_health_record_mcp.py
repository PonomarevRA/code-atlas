"""Agent access to immutable health pages without another source-analysis pass."""

from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field


def register_health_record_tool(server, request, selected_project, base):
    """Register one reader; record ownership is checked by the Atlas service."""
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_code_health_record(
        recordId: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")],
        projectId: str | None = None,
        kind: Literal["assessment", "plan"] = "assessment",
        offset: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 10,
    ) -> dict:
        """Continue a truncated baseline/forecast without recapturing or parsing source.

        recordId is assessmentId or planId; select kind=plan for a forecast.
        Follow nextOffset until null. items[].healthIdentity is the original
        baseline identity for projections. Dates and sourceScopeTruncated refer
        to stored evidence; responseTruncated/itemTruncated describe delivery.
        Missing/expired records stay UNKNOWN. recordUrl exposes full retained facts.
        """
        project = quote(selected_project(projectId), safe="")
        path = f"/api/projects/{project}/code-health/records/{recordId}/page"
        result = await request("GET", path + f"?kind={kind}&offset={offset}&limit={limit}")
        result["recordUrl"] = base + path.removesuffix("/page") + f"?kind={kind}"
        return result
