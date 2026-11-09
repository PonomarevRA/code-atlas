"""Bounded explicit MCP operations for structural search and incremental proposals."""

from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field

from .mcp_response_budget import json_bytes
from .module_proposal_api import ModuleConstraints, ModuleIncrementalInput, ModuleSearchInput, ModuleStateReference


MODULE_PROPOSAL_TOOLS = ['search_code_module_partitions', 'update_code_module_partition']
Identity = Annotated[str, Field(min_length=1, max_length=128)]


def register_module_proposal_tools(server, request, selected_project):
    """
    Register opt-in requests on the existing selected-project transport.

    Args:
        server: FastMCP owner; discovery performs no computation.
        request: Fixed-origin existing loopback transport coroutine.
        selected_project: Existing project-binding guard; never replaced by caller graph facts.
    Returns:
        None. New tools are explicitly part of the source tool surface.
    """
    async def call(project_id, operation, body):
        """Use one pinned project and reject oversized responses without slicing partitions."""
        path = '/api/projects/' + quote(selected_project(project_id), safe='') + '/' + operation
        # Keep omitted nested constraints omitted: their defaults depend on the
        # captured projection, rather than the transport schema's upper bounds.
        result = await request('POST', path, body.model_dump(exclude_unset=True))
        if json_bytes(result) > 64_000:
            raise ValueError('Module proposal exceeds MCP budget; narrow the scope or proposal count.')
        return result

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def search_code_module_partitions(
        snapshotId: Identity, projectId: str | None = None,
        level: Literal['folder', 'class'] = 'folder',
        folders: Annotated[list[str], Field(max_length=32)] | None = None,
        currentPartition: Annotated[list[list[str]], Field(max_length=128)] | None = None,
        constraints: ModuleConstraints | None = None,
        seed: Annotated[int, Field(strict=True, ge=0, le=4294967295)] = 42,
        evaluationsPerMethod: Annotated[int, Field(strict=True, ge=1, le=256)] = 64,
        maxProposals: Annotated[int, Field(strict=True, ge=1, le=16)] = 8,
    ) -> dict:
        """
        Find finite Pareto proposals balancing explicit Atlas MQ and changed membership.

        At most128 captured class/folder units. Current partition must satisfy hard
        constraints; omission uses separate captured units. GA/random/local receive
        equal evaluation/attempt caps; SArF/Leiden generation is timed separately.
        Results are proposals, not global optima or semantic architecture qualification.
        Files, default grouping and geometry remain unchanged.
        """
        body = ModuleSearchInput(snapshotId=snapshotId, level=level, folders=folders,
            currentPartition=currentPartition, constraints=constraints, seed=seed,
            evaluationsPerMethod=evaluationsPerMethod, maxProposals=maxProposals)
        return await call(projectId, 'module-search', body)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    async def update_code_module_partition(
        snapshotId: Identity, projectId: str | None = None,
        level: Literal['folder', 'class'] = 'folder',
        folders: Annotated[list[str], Field(max_length=32)] | None = None,
        stateRef: ModuleStateReference | None = None,
        initialPartition: Annotated[list[list[str]], Field(max_length=200)] | None = None,
        maxEvaluations: Annotated[int, Field(strict=True, ge=1, le=16384)] = 4096,
    ) -> dict:
        """
        Initialize or update a private hierarchy from host-captured snapshots.

        Omit stateRef initially, then pass the returned reference with a new snapshot.
        Old graphs/state are never accepted from caller JSON. Unknown/incomplete delta
        triggers explicit full refresh; this is atlas-incremental-directed, not exact
        LD-Leiden. Membership and geometry are separate. No map/source is changed.
        References share existing AnalysisContexts quotas/expiry. Release each unused
        handle via close_analysis_context using its contextId and original snapshotId.
        """
        body = ModuleIncrementalInput(snapshotId=snapshotId, level=level, folders=folders,
            stateRef=stateRef, initialPartition=initialPartition, maxEvaluations=maxEvaluations)
        return await call(projectId, 'module-updates', body)
