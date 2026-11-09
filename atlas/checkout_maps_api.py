"""Explicit temporary-checkout HTTP scope; permanent ProjectStore is never selected."""

from typing import Annotated

from fastapi import FastAPI, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .checkout_maps import CheckoutMapError, CheckoutMaps
from .code_health_comparison import compare_assessments
from .code_health_models import HealthQuery, HealthPlanQuery, HealthCompareQuery
from .code_health_planning import plan_changes
from .execution_path import ExecutionPathError
from .mcp_response_budget import json_bytes
from .node_description import build_node_description
from .store import StoreError


SessionId = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]
MAX_RESPONSE_BYTES = 64_000


class UnavailableCheckoutMaps:
    """Keep an unsafe temporary namespace unavailable while permanent Atlas stays usable."""

    def start(self):
        """No worker starts for an unsafe or unreadable temporary namespace."""

    def close(self):
        """There are no owned temporary workers to stop."""

    def __getattr__(self, name):
        """Return a stable failure for explicit feature calls without touching unsafe storage."""
        def unavailable(*args, **kwargs):
            raise CheckoutMapError('Temporary checkout maps are unavailable; inspect their storage/settings.',
                                   503, 'checkout_maps_unavailable')
        return unavailable


def create_checkout_maps(data_dir, runtime_selector):
    """
    Create the optional private namespace without making it a permanent-store startup gate.

    Args:
        data_dir: Host-owned application data root, never an HTTP-supplied directory.
        runtime_selector: Trusted zero-argument canonical runtime selector; read once, never changed.
    Returns:
        CheckoutMaps or an unavailable facade that preserves corrupt metadata for inspection.
    """
    try:
        return CheckoutMaps(data_dir, runtime_python=runtime_selector())
    except (CheckoutMapError, OSError, ValueError):
        return UnavailableCheckoutMaps()


class CheckoutEnsureInput(BaseModel):
    """Select an existing checkout and caller task key; TTL is server-owned."""
    model_config = ConfigDict(extra='forbid')
    checkout: str = Field(min_length=1, max_length=4096)
    ownerKey: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,100}$')
    label: str | None = Field(default=None, max_length=160)


class CheckoutSearchInput(BaseModel):
    """Search the current private snapshot or enforce an exact expected revision."""
    model_config = ConfigDict(extra='forbid')
    query: str = Field(default='', max_length=500)
    snapshotId: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=40, ge=1, le=120, strict=True)


class CheckoutNodeInput(BaseModel):
    """Pin an existing raw node inside one private snapshot."""
    model_config = ConfigDict(extra='forbid')
    snapshotId: str = Field(min_length=1, max_length=128)
    nodeId: str = Field(min_length=1, max_length=4096)


def _bounded(session_id, result):
    """
    Attach explicit session identity before a read can renew its lifetime.

    Args:
        session_id: Selected opaque temporary session, never a filesystem path.
        result: JSON facts produced by existing Atlas readers in that private store.
    Returns:
        Response retaining original project/snapshot identities and temporary scope.
    Raises:
        StoreError: More than 64000 encoded bytes; callers must narrow their scope.
    """
    response = {'checkoutSessionId': session_id, 'temporary': True, **result}
    if json_bytes(response) > MAX_RESPONSE_BYTES:
        raise StoreError('Temporary-map response is too large; narrow the query or file scope.', 413)
    return response


def register_checkout_routes(app: FastAPI, sessions) -> None:
    """
    Register private map lifecycle and existing source/health operations.

    Args:
        app: Host application that already enforces local HTTP boundaries.
        sessions: Lifecycle-owned CheckoutMaps; access leases pin only private stores.
    Returns:
        None. Registration creates no session and triggers no rebuild.
    """
    @app.exception_handler(CheckoutMapError)
    async def checkout_error(request, error):
        """Expose the controlled checkout failure without a traceback."""
        return JSONResponse({'error': str(error), 'code': error.code}, status_code=error.status)

    @app.post('/api/checkout-sessions', status_code=202)
    def ensure(body: CheckoutEnsureInput):
        """Create or reuse only this owner/checkout's private map; never a permanent registration."""
        return sessions.ensure(body.checkout, body.ownerKey, body.label)

    @app.get('/api/checkout-sessions')
    def list_sessions():
        """List settings metadata without extending any lifetime."""
        return sessions.list()

    @app.get('/api/checkout-sessions/{session_id}')
    def status(session_id: SessionId):
        """Poll build/deletion progress without keepalive."""
        return sessions.status(session_id)

    @app.post('/api/checkout-sessions/{session_id}/rebuild', status_code=202)
    def rebuild(session_id: SessionId):
        """Rebuild only this private store; other agents and permanent maps are unchanged."""
        return sessions.rebuild(session_id)

    @app.delete('/api/checkout-sessions/{session_id}')
    def finish(session_id: SessionId):
        """Retire immediately; physical deletion waits for owned readers/builders to drain."""
        return sessions.finish(session_id)

    @app.post('/api/checkout-sessions/{session_id}/search')
    def search(session_id: SessionId, body: CheckoutSearchInput):
        """Search one actual Atlas snapshot; only a successfully bounded read renews TTL."""
        with sessions.access(session_id) as entry:
            result = entry.store.search_project_nodes(entry.project_id, body.snapshotId,
                                                      body.query, body.limit, None)
            entry.store.assert_snapshot(entry.project_id, result['snapshotId'])
            return _bounded(session_id, result)

    @app.post('/api/checkout-sessions/{session_id}/describe')
    def describe(session_id: SessionId, body: CheckoutNodeInput):
        """Reuse Atlas node/source evidence and preserve stale-snapshot failures."""
        with sessions.access(session_id) as entry:
            def read_description(root, graph):
                """Keep the audited builder read-only while the session lease pins its store."""
                return build_node_description(root, graph, body.nodeId, body.snapshotId)

            try:
                result = entry.store._read_snapshot_analysis(entry.project_id, body.snapshotId,
                                                             read_description)
            except ExecutionPathError as error:
                raise StoreError(str(error), error.status) from error
            return _bounded(session_id, result)

    @app.post('/api/checkout-sessions/{session_id}/health')
    def health(session_id: SessionId, body: HealthQuery):
        """Capture actual health in the private store; working-tree mode reads but never writes checkout."""
        with sessions.access(session_id) as entry:
            result = entry.health.assess(entry.project_id, body)
            entry.store.assert_snapshot(entry.project_id, result['snapshotId'])
            return _bounded(session_id, result)

    @app.post('/api/checkout-sessions/{session_id}/plan')
    def plan(session_id: SessionId, body: HealthPlanQuery):
        """Estimate from this session's baseline and drafts/ranges; never persist drafts to source."""
        with sessions.access(session_id) as entry:
            return _bounded(session_id, plan_changes(entry.health, entry.project_id, body))

    @app.post('/api/checkout-sessions/{session_id}/compare')
    def compare(session_id: SessionId, body: HealthCompareQuery):
        """Compare immutable private assessments, rejecting an unrelated forecast baseline."""
        with sessions.access(session_id) as entry:
            records = entry.health.records
            before = records.read(entry.project_id, body.beforeAssessmentId)
            after = records.read(entry.project_id, body.afterAssessmentId)
            plan = records.read(entry.project_id, body.planId, 'plan') if body.planId else None
            if plan and plan['baselineAssessmentId'] != body.beforeAssessmentId:
                raise StoreError('Plan belongs to another baseline', 409)
            result = compare_assessments(before, after, identity_map=body.identityMap,
                                         plan=plan, locale=body.locale)
            if body.planId and plan is None:
                result['planComparisonStatus'] = 'unavailable'
            return _bounded(session_id, result)
