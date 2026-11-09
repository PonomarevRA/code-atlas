"""Bounded English agent tools proxy the single Atlas health service."""

from typing import Annotated
from urllib.parse import quote
from mcp.types import ToolAnnotations
from pydantic import Field
from .code_health_models import HealthQuery, HealthPlanQuery, HealthCompareQuery, DraftFile, HealthProjection
from .mcp_response_budget import bound_list_response, json_bytes, MCP_RESPONSE_BYTES

HEALTH_TOOLS = ["get_code_health", "get_code_health_report", "get_code_health_record", "plan_code_changes", "compare_code_health"]


def bounded_health(result: dict, *, budget: int = MCP_RESPONSE_BYTES) -> dict:
    """Keep immutable IDs, dates and uncertainty while explicitly bounding large lists."""
    compact = dict(result)
    if isinstance(result.get("nodes"), list):
        compact["nodes"] = []
        for node in result["nodes"]:
            documentation = node.get("documentationHealth")
            has_contract = isinstance(documentation, dict) and "contract" in documentation
            if "lexicalCalls" not in node and not has_contract:
                compact["nodes"].append(node)
                continue
            item = {key: value for key, value in node.items() if key != "lexicalCalls"}
            if has_contract:
                item["documentationHealth"] = {key: value for key, value in node["documentationHealth"].items() if key != "contract"}
            compact["nodes"].append(item)
    bounded = bound_list_response(compact, ("nodes", "projections", "changes", "planComparison", "findings"), flag="responseTruncated", budget=budget)
    if bounded is not None:
        return bounded
    return {key: result.get(key) for key in ("assessmentId", "planId", "status", "policyVersion", "measuredAt", "graphUpdatedAt")} | {"responseTruncated": True, "warning": "Narrow the source scope; full record remains in Atlas."}


def register_health_tools(server, request, selected_project):
    """Health records never execute project code or trigger an implicit graph rebuild."""
    read = ToolAnnotations(readOnlyHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

    def path(project):
        return "/api/projects/" + quote(selected_project(project), safe="") + "/code-health"

    @server.tool(annotations=write)
    async def get_code_health(projectId: str | None = None, sourceMode: str = "snapshot", snapshotId: str | None = None,
                              nodeIds: list[str] | None = None, files: list[str] | None = None, maxMethods: int = 120) -> dict:
        """Capture an immutable baseline. Choose working-tree and affected files before authorized edits.

        Returns health, transparency, documentation, file/folder organization, dependencies and source evidence.
        Organization counts cover the explicit selected files, not a whole filesystem. Unknowns stay null.
        Snapshot reads can be stale; inspect dates, never rebuild repeatedly while editing. No source mutation.
        Inspect documentationHealth.parameterCoverage and missing names; maintain meaningful input descriptions.
        Documentation has a capped small contribution and cannot compensate for structural findings or unknowns.
        Full selected files include new helpers/deferred bodies. Health is advisory, not test or semantic proof.
        """
        query = HealthQuery(sourceMode=sourceMode, snapshotId=snapshotId, nodeIds=nodeIds or [], files=files or [], maxMethods=maxMethods)
        return bounded_health(await request("POST", path(projectId), query.model_dump()))

    @server.tool(annotations=read)
    async def get_code_health_report(projectId: str | None = None, snapshotId: str | None = None,
                                     limit: int = 40, cursor: int = 0, order: str = "health") -> dict:
        """Read paginated captured hotspots and coverage without scanning source. Null scores remain unknown."""
        if not 1 <= limit <= 120 or cursor < 0 or order not in {"health", "documentation"}:
            raise ValueError("Invalid report pagination/order")
        suffix = f"/report?limit={limit}&cursor={cursor}&order={order}"
        if snapshotId:
            suffix += "&snapshotId=" + quote(snapshotId, safe="")
        source = await request("GET", path(projectId) + suffix)
        # Reserve the cursor's serialized width before trimming, including the
        # last API page whose null cursor must become a continuation after MCP truncation.
        result = bounded_health(source, budget=MCP_RESPONSE_BYTES - json_bytes(cursor + limit) - 16)
        if len(result.get("nodes", [])) < len(source.get("nodes", [])):
            delivered = len(result.get("nodes", []))
            if not delivered:
                raise ValueError("A report row exceeds the MCP budget. Find its node with search_nodes, capture get_code_health(nodeIds=[...]), then page get_code_health_record.")
            result["nextCursor"] = cursor + delivered
        return result

    @server.tool(annotations=write)
    async def plan_code_changes(baselineAssessmentId: str, intent: str, projectId: str | None = None,
                                drafts: Annotated[list[DraftFile], Field(max_length=20)] | None = None,
                                projections: Annotated[list[HealthProjection], Field(max_length=120)] | None = None) -> dict:
        """Before editing, estimate health from full draft file text OR explicit metric ranges and reasons.

        Choose exactly one input mode; omit the other.
        drafts: [{"file":"src/example.py","content":"complete proposed file text"}].
        projections: [{"identity":"COPY nodes[].healthIdentity FROM THE BASELINE","reason":"Explain this method's change",
                       "metrics":{"maxNestingDepth":{"min":0,"max":1}}}].
        Projection identity is a baseline method identity, never a file path or raw node ID.
        file/change objects are not projections. For file-based estimates supply complete drafts.
        Output is estimated: unknown components widen ranges, unchanged files are assumptions, source is never written.
        Preserve contracts, errors/cancellation and useful prose. Do not invent documentation or split code to game scores.
        Full drafts recompute file/folder organization across the retained scope. Include old files as empty drafts
        when modeling a move, and new full files; projected method ranges assume organization unchanged.
        """
        query = HealthPlanQuery(baselineAssessmentId=baselineAssessmentId, intent=intent, drafts=drafts or [], projections=projections or [])
        return bounded_health(await request("POST", path(projectId) + "/plan", query.model_dump(), timeout=60))

    @server.tool(annotations=read)
    async def compare_code_health(beforeAssessmentId: str, afterAssessmentId: str, projectId: str | None = None,
                                  planId: str | None = None, identityMap: dict[str, str] | None = None) -> dict:
        """Compare measured before/after plus forecast ranges. Include new helpers and report renamed/unknown declarations.

        PASS/REVIEW/UNKNOWN describes policy findings, never behavioral tests. Supply explicit identityMap for renames.
        Measure affected working-tree files after edits; rebuild the graph once after the completed change series.
        """
        query = HealthCompareQuery(beforeAssessmentId=beforeAssessmentId, afterAssessmentId=afterAssessmentId, planId=planId, identityMap=identityMap or {})
        return bounded_health(await request("POST", path(projectId) + "/compare", query.model_dump()))
