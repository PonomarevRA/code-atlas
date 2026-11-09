"""MCP adapter for bounded module analysis, using the same core HTTP contract."""

from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field

from .mcp_response_budget import json_bytes


MODULE_ANALYSIS_TOOLS = ["analyze_code_modules"]


def register_module_analysis_tools(server, request, selected_project) -> None:
    """Expose an explicit analysis call; ordinary map reads keep their existing cost."""
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def analyze_code_modules(
        snapshotId: Annotated[str, Field(min_length=1, max_length=128)],
        projectId: str | None = None,
        level: Literal["folder", "class"] = "folder",
        folders: Annotated[list[str], Field(max_length=32)] | None = None,
        algorithms: Annotated[
            list[Literal["current", "louvain", "leiden", "sarf-simple-directed"]],
            Field(min_length=1, max_length=4),
        ] = ["current", "louvain", "leiden"],
        seed: Annotated[int, Field(strict=True, ge=0, le=4294967295)] = 42,
        resolution: Annotated[float, Field(gt=0, le=10, strict=True, allow_inf_nan=False)] = 1.0,
    ) -> dict:
        """
        Compare class/folder module proposals on one pinned snapshot without edits.

        Use folders to narrow a large map. Unknown ownership/binding remains
        partial; Q is not semantic architecture quality. Unavailable providers
        are reported explicitly. SArF is the named simple directed adaptation,
        not a reproduction of member-aware paper experiments.
        """
        project_id = selected_project(projectId)
        body = {
            "snapshotId": snapshotId, "level": level, "folders": folders,
            "algorithms": algorithms, "seed": seed, "resolution": resolution,
        }
        path = "/api/projects/" + quote(project_id, safe="") + "/module-analysis"
        result = await request("POST", path, body)
        if json_bytes(result) > 64_000:
            raise ValueError("Module response exceeds MCP budget; select a smaller folder scope.")
        return result
