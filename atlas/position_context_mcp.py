"""Explicit agent entry points for bounded file/line dependency context."""

from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field

from .mcp_response_budget import json_bytes
from .position_context import PositionContextInput


POSITION_CONTEXT_TOOLS = ['get_position_dependencies', 'get_checkout_position_dependencies']
Identity = Annotated[str, Field(min_length=1, max_length=128)]
File = Annotated[str, Field(min_length=1, max_length=4096)]
Line = Annotated[int, Field(strict=True, ge=1)]
Depth = Annotated[int, Field(strict=True, ge=1, le=8)]
NodeBudget = Annotated[int, Field(strict=True, ge=1, le=120)]
EdgeBudget = Annotated[int, Field(strict=True, ge=1, le=240)]
SourceHash = Annotated[str, Field(pattern=r'^[a-fA-F0-9]{64}$')]
Session = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]


def register_position_context_tools(server, request, selected_project, project_id=None, data_map_id=None):
    """Register fixed-origin proxies, preserving configured project and temporary-map scopes.

    Discovery performs no indexing, source read or IDE attachment. Both tools use
    the HTTP model; analyzed content never becomes an instruction or executable.
    """
    async def read(path, body):
        """Reject an oversized final response rather than hiding identities or dropping evidence."""
        result = await request('POST', path, body.model_dump())
        if json_bytes(result) > 64_000:
            raise ValueError('Position context exceeds MCP budget; reduce depth/maxNodes/maxEdges.')
        return result

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def get_position_dependencies(
        snapshotId: Identity, file: File, line: Line, projectId: str | None = None,
        depth: Depth = 2, direction: Literal['incoming', 'outgoing', 'both'] = 'both',
        maxNodes: NodeBudget = 60, maxEdges: EdgeBudget = 120,
        expectedSourceHash: SourceHash | None = None,
    ) -> dict:
        """
        Find nearest callers/callees around a file and a 1-based line in a pinned project.

        Incoming and outgoing paths are traversed separately through calls and
        dispatches_to, at depth 2 by default. Inspect status, candidates, freshness,
        truncation and sourceEvidence before drawing a conclusion. Static edges
        are possible dependencies, not runtime/data-flow proof. A null literal or
        repeated condition is evidence for agent review, not a certified defect.
        Use expectedSourceHash to fence saved editor bytes. Unsaved buffers and
        automatic IDE highlighting are not implemented; no file/map is modified.
        """
        if data_map_id is not None:
            raise ValueError('Use a code-project Atlas connection for source dependencies.')
        selected = selected_project(projectId)
        body = PositionContextInput(snapshotId=snapshotId, file=file, line=line, depth=depth,
            direction=direction, maxNodes=maxNodes, maxEdges=maxEdges, expectedSourceHash=expectedSourceHash)
        return await read('/api/projects/' + quote(selected, safe='') + '/position-dependencies', body)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def get_checkout_position_dependencies(
        sessionId: Session, snapshotId: Identity, file: File, line: Line,
        depth: Depth = 2, direction: Literal['incoming', 'outgoing', 'both'] = 'both',
        maxNodes: NodeBudget = 60, maxEdges: EdgeBudget = 120,
        expectedSourceHash: SourceHash | None = None,
    ) -> dict:
        """
        Read file/line dependency context only from this temporary checkout session.

        Ensure the private map first and wait for a snapshot; never use a permanent
        project ID as a session. Successful useful reads renew the existing one-hour
        TTL. Validation, stale snapshot and oversized-response failures do not.
        Results preserve possible bindings and source uncertainty; no nullability
        or redundant-guard proof is implied. Defaults/limits match the project tool.
        """
        if project_id is not None or data_map_id is not None:
            raise ValueError('Use an unbound Atlas MCP connection for a separate checkout session.')
        body = PositionContextInput(snapshotId=snapshotId, file=file, line=line, depth=depth,
            direction=direction, maxNodes=maxNodes, maxEdges=maxEdges, expectedSourceHash=expectedSourceHash)
        return await read('/api/checkout-sessions/' + quote(sessionId, safe='') + '/position-dependencies', body)
