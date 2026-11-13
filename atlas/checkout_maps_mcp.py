"""Explicit private-checkout agent tools; no hidden global current-map selection."""

from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field

from .code_health_models import DraftFile, HealthCompareQuery, HealthPlanQuery, HealthProjection, HealthQuery
from .mcp_response_budget import json_bytes
from .mcp_transport import CheckoutBuildPending


CHECKOUT_TOOLS = [
    'ensure_checkout_map', 'ensure_checkout_map_with_reclaim', 'list_checkout_maps',
    'get_checkout_map', 'rebuild_checkout_map',
    'open_worktree_overlay', 'refresh_worktree_overlay', 'search_worktree_overlay',
    'finish_checkout_map', 'search_checkout_nodes', 'search_checkout_file', 'describe_checkout_node',
    'get_checkout_code_health', 'plan_checkout_code_changes', 'compare_checkout_code_health',
]
Session = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]
Identity = Annotated[str, Field(min_length=1, max_length=128)]


def register_checkout_tools(server, request, project_id=None, data_map_id=None):
    """
    Register bounded temporary-map tools without relaxing configured project scope.

    Args:
        server: FastMCP composition owner.
        request: Existing loopback transport coroutine, with fixed origin and byte limits.
        project_id: Existing canonical-project binding; temporary access is refused when bound.
        data_map_id: Existing data-map binding; temporary access is refused when bound.
    Returns:
        None; no session or process is started by tool discovery.
    """
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

    def path(session_id=None, suffix=''):
        """Check the existing connection scope before forming one fixed API path."""
        if project_id is not None or data_map_id is not None:
            raise ValueError('Use an unbound Atlas MCP connection for a separate checkout session.')
        base = '/api/checkout-sessions'
        return base + ('/' + quote(session_id, safe='') if session_id is not None else '') + suffix

    async def call(method, session_id=None, suffix='', body=None):
        """Use the same explicit session in transport and enforce the final MCP byte budget."""
        if method == 'POST' and body is None:
            body = {}
        result = await request(method, path(session_id, suffix), body)
        if json_bytes(result) > 64_000:
            raise ValueError('Temporary-map response exceeds the MCP budget; narrow the scope.')
        return result

    @server.tool(annotations=write)
    async def ensure_checkout_map(
        checkout: Annotated[str, Field(min_length=1, max_length=4096)],
        ownerKey: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,100}$')],
        label: Annotated[str, Field(max_length=160)] | None = None,
        overlayBase: Annotated[bool, Field(strict=True)] = False,
    ) -> dict:
        """
        Automatically create/reuse this task's private Atlas map for an absolute checkout.

        Use a stable task-specific ownerKey. Different agents/tasks receive separate folders;
        permanent registrations/maps and checkout files stay unchanged. The map expires one
        hour after its last successful useful read. Poll get_checkout_map while building;
        polling, list and ensure reuse do not keep it alive. Use search_checkout_file for a
        known source path during the build. Never infer graph readiness from a handle.
        Set overlayBase only to build a clean, exact Git root for the worktree pilot.
        """
        return await call('POST', body={'checkout': checkout, 'ownerKey': ownerKey,
                                       'label': label, 'overlayBase': overlayBase})

    @server.tool(annotations=write)
    async def open_worktree_overlay(baseSessionId: Session, baseSnapshotId: Session,
                                    checkout: Annotated[str, Field(min_length=1, max_length=4096)]) -> dict:
        """Open an experimental overlay at the same commit in a sibling Git worktree.

        Use a completed ensure_checkout_map(overlayBase=true) build from a clean Git root.
        An overlay is a process-local handle and does not build or copy the base graph.
        """
        return await call('POST', suffix='/overlays', body={
            'baseSessionId': baseSessionId, 'baseSnapshotId': baseSnapshotId, 'checkout': checkout,
        })

    @server.tool(annotations=write)
    async def refresh_worktree_overlay(overlayId: Session) -> dict:
        """Capture current raw source changes once and return a revision for parallel reads."""
        return await call('POST', suffix=f'/overlays/{overlayId}/refresh')

    @server.tool(annotations=read)
    async def search_worktree_overlay(overlayId: Session,
                                      query: Annotated[str, Field(max_length=500)] = '',
                                      limit: Annotated[int, Field(strict=True, ge=1, le=40)] = 20,
                                      overlayRevision: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')] | None = None) -> dict:
        """Search shared baseline nodes and current changed-file declarations separately.

        Baseline nodes retain base snapshot IDs. Current declarations have source hashes,
        not graph IDs. Link facts require a full private checkout build.
        Pass overlayRevision from open/refresh for fast reads of that captured state.
        Omit it to refresh automatically after edits. An old pinned revision is rejected
        after a different epoch has been captured.
        """
        return await call('POST', suffix=f'/overlays/{overlayId}/search',
                          body={'query': query, 'limit': limit, 'overlayRevision': overlayRevision})

    @server.tool(annotations=destructive)
    async def ensure_checkout_map_with_reclaim(
        checkout: Annotated[str, Field(min_length=1, max_length=4096)],
        ownerKey: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,100}$')],
        label: Annotated[str, Field(max_length=160)] | None = None,
    ) -> dict:
        """Retry a full checkout catalog by replacing at most one idle temporary map.

        Retry with the same checkout and ownerKey. At capacity Atlas selects the
        longest-idle map (at least 20 minutes), excluding current readers and builders.
        The result reports reclaimed.sessionId and idleSeconds when a map was removed.
        Its temporary analysis data is deleted. Permanent maps and sources are outside this operation.
        """
        return await call('POST', suffix='/ensure-with-reclaim',
                          body={'checkout': checkout, 'ownerKey': ownerKey, 'label': label})

    @server.tool(annotations=read)
    async def list_checkout_maps(
        offset: Annotated[int, Field(strict=True, ge=0, le=8)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=4)] = 4,
    ) -> dict:
        """
        Page temporary folders/expiry without renewing them; nextOffset continues the current list.

        This metadata catalog is not snapshot-pinned: concurrent creation/deletion may change
        ordering. reclaim reports current inactivity and eligibility when supported.
        Use get_checkout_map for a selected session's complete build diagnostics.
        """
        catalog = await request('GET', path(), None)
        rows = catalog['sessions']
        selected = []
        # Project/job diagnostics are intentionally excluded from this catalog. Exact
        # folder/source identity and expiry remain available even with long error text.
        keys = ('sessionId', 'projectId', 'temporary', 'path', 'checkout', 'ownerKey',
                'label', 'state', 'ttlSeconds', 'createdAt', 'expiresAt', 'activeLeases')
        for row in rows[offset:offset+limit]:
            item = {key: row[key] for key in keys}
            if 'reclaim' in row:
                item['reclaim'] = row['reclaim']
            project = row.get('project') or {}
            item['project'] = {key: project.get(key) for key in ('id', 'snapshotId', 'status')}
            selected.append(item)
        next_offset = offset+len(selected)
        result = {'sessions': selected, 'pagination': {'offset': offset, 'limit': limit,
                  'total': len(rows), 'nextOffset': next_offset if next_offset < len(rows) else None},
                  'diagnostics': 'Use get_checkout_map(sessionId) for full build/deletion details.'}
        if json_bytes(result) > 64_000:
            raise ValueError('Temporary-map catalog exceeds the MCP budget; reduce limit.')
        return result

    @server.tool(annotations=read)
    async def get_checkout_map(sessionId: Session) -> dict:
        """Poll one build/expiry/deletion state; this metadata read is not keepalive."""
        return await call('GET', sessionId)

    @server.tool(annotations=write)
    async def rebuild_checkout_map(sessionId: Session) -> dict:
        """Explicitly rebuild only the private map after a completed series of source changes."""
        return await call('POST', sessionId, '/rebuild')

    @server.tool(annotations=destructive)
    async def finish_checkout_map(sessionId: Session) -> dict:
        """Retire this private map; deletion waits for its owned readers/builders, never deletes source."""
        return await call('DELETE', sessionId)

    @server.tool(annotations=read)
    async def search_checkout_nodes(sessionId: Session,
                                    query: Annotated[str, Field(max_length=500)] = '',
                                    snapshotId: Identity | None = None,
                                    limit: Annotated[int, Field(strict=True, ge=1, le=120)] = 40) -> dict:
        """
        Find actual raw nodes in the temporary Atlas map; save snapshotId for later reads.

        Before the first snapshot, unpinned search may return state=building, retryAfterMs
        and nextCalls. Continue with the same session; the response carries no search results.
        """
        try:
            return await call('POST', sessionId, '/search', {'query': query, 'snapshotId': snapshotId, 'limit': limit})
        except CheckoutBuildPending as pending:
            return {**pending.details, 'temporary': True,
                    'message': 'Atlas строит карту. Для известного файла можно искать объявления уже сейчас.',
                    'nextCalls': [
                        {'tool': 'get_checkout_map', 'arguments': {'sessionId': sessionId}},
                        {'tool': 'search_checkout_file', 'arguments': {'sessionId': sessionId}, 'requires': ['path']},
                    ]}

    @server.tool(annotations=read)
    async def search_checkout_file(sessionId: Session,
                                   path: Annotated[str, Field(min_length=1, max_length=4096)],
                                   query: Annotated[str, Field(max_length=500)] = '',
                                   limit: Annotated[int, Field(strict=True, ge=1, le=40)] = 20) -> dict:
        """
        Search static declarations in one exact repository-relative file, even while a map builds.

        Matches use a case-insensitive substring of the lexical name, optionally qualified by
        its owner. Coordinates and sourceHash identify the working-tree bytes. Use get_checkout_map
        and search_checkout_nodes for graph IDs and dependency work after snapshot publication.
        A partial or empty result applies only to the requested file and reported coverage.
        """
        return await call('POST', sessionId, '/file-search', {'path': path, 'query': query, 'limit': limit})

    @server.tool(annotations=read)
    async def describe_checkout_node(sessionId: Session, snapshotId: Identity,
                                     nodeId: Annotated[str, Field(min_length=1, max_length=4096)]) -> dict:
        """Read source-backed facts, branches and readabilityRecommendations in the exact private snapshot."""
        return await call('POST', sessionId, '/describe', {'snapshotId': snapshotId, 'nodeId': nodeId})

    @server.tool(annotations=write)
    async def get_checkout_code_health(sessionId: Session, snapshotId: Identity,
                                       sourceMode: Literal['snapshot', 'working-tree'] = 'working-tree',
                                       files: Annotated[list[str], Field(max_length=20)] | None = None,
                                       nodeIds: Annotated[list[str], Field(max_length=120)] | None = None,
                                       maxMethods: Annotated[int, Field(strict=True, ge=1, le=120)] = 40) -> dict:
        """
        Capture a private immutable baseline before edits, using affected checkout files.

        Source is read but never modified; health is advisory and unknowns remain explicit.
        Reduce files/maxMethods if the result exceeds the response budget.
        """
        body = HealthQuery(snapshotId=snapshotId, sourceMode=sourceMode, files=files or [],
                           nodeIds=nodeIds or [], maxMethods=maxMethods)
        return await call('POST', sessionId, '/health', body.model_dump())

    @server.tool(annotations=write)
    async def plan_checkout_code_changes(sessionId: Session, baselineAssessmentId: Session,
                                         intent: Annotated[str, Field(min_length=1, max_length=2000)],
                                         drafts: Annotated[list[DraftFile], Field(max_length=20)] | None = None,
                                         projections: Annotated[list[HealthProjection], Field(max_length=120)] | None = None) -> dict:
        """
        Forecast from this session's baseline and complete draft files OR explicit metric ranges.

        Supply exactly one input mode. Drafts are never written into the checkout;
        projections need baseline healthIdentity, reason and ranges, not file/change objects.
        """
        body = HealthPlanQuery(baselineAssessmentId=baselineAssessmentId, intent=intent,
                               drafts=drafts or [], projections=projections or [])
        return await call('POST', sessionId, '/plan', body.model_dump())

    @server.tool(annotations=read)
    async def compare_checkout_code_health(sessionId: Session, beforeAssessmentId: Session,
                                           afterAssessmentId: Session, planId: Session | None = None,
                                           identityMap: Annotated[dict[str, str], Field(max_length=120)] | None = None) -> dict:
        """Compare private before/after records and an optional forecast; this does not certify behavior."""
        body = HealthCompareQuery(beforeAssessmentId=beforeAssessmentId, afterAssessmentId=afterAssessmentId,
                                  planId=planId, identityMap=identityMap or {})
        return await call('POST', sessionId, '/compare', body.model_dump())
