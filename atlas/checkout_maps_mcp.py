"""Explicit private-checkout agent tools; no hidden global current-map selection."""

from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field

from .code_health_models import DraftFile, HealthCompareQuery, HealthPlanQuery, HealthProjection, HealthQuery
from .mcp_response_budget import json_bytes


CHECKOUT_TOOLS = [
    'ensure_checkout_map', 'list_checkout_maps', 'get_checkout_map', 'rebuild_checkout_map',
    'finish_checkout_map', 'search_checkout_nodes', 'describe_checkout_node',
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
    ) -> dict:
        """
        Automatically create/reuse this task's private Atlas map for an absolute checkout.

        Use a stable task-specific ownerKey. Different agents/tasks receive separate folders;
        permanent registrations/maps and checkout files stay unchanged. The map expires one
        hour after its last successful useful read. Poll get_checkout_map while building;
        polling, list and ensure reuse do not keep it alive. Never infer readiness from a handle.
        """
        return await call('POST', body={'checkout': checkout, 'ownerKey': ownerKey, 'label': label})

    @server.tool(annotations=read)
    async def list_checkout_maps(
        offset: Annotated[int, Field(strict=True, ge=0, le=8)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=4)] = 4,
    ) -> dict:
        """
        Page temporary folders/expiry without renewing them; nextOffset continues the current list.

        This metadata catalog is not snapshot-pinned: concurrent creation/deletion may change
        ordering. Use get_checkout_map for a selected session's complete build diagnostics.
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

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
    async def finish_checkout_map(sessionId: Session) -> dict:
        """Retire this private map; deletion waits for its owned readers/builders, never deletes source."""
        return await call('DELETE', sessionId)

    @server.tool(annotations=read)
    async def search_checkout_nodes(sessionId: Session,
                                    query: Annotated[str, Field(max_length=500)] = '',
                                    snapshotId: Identity | None = None,
                                    limit: Annotated[int, Field(strict=True, ge=1, le=120)] = 40) -> dict:
        """Find actual raw nodes in the temporary Atlas map; save returned snapshotId for later reads."""
        return await call('POST', sessionId, '/search', {'query': query, 'snapshotId': snapshotId, 'limit': limit})

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
