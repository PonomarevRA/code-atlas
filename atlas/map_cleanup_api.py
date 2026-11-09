"""
Bounded explicit cleanup preview/confirmation/status routes; no arbitrary client path is accepted.
"""
from typing import Literal
from pydantic import BaseModel, Field, StrictBool, model_validator
from fastapi.responses import JSONResponse

from .map_cleanup_inventory import CleanupError


class CleanupSelection(BaseModel):
    """
    Select one registered code/data identity, never a source/storage pathname.
    """
    kind: Literal['code', 'data']
    id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    model_config = {'extra': 'forbid'}


class CleanupPreview(BaseModel):
    """
    Bind one action to selected catalog IDs; omitted action retains the existing snapshot-clear behavior.
    """
    maps: list[CleanupSelection] = Field(min_length=1, max_length=50)
    action: Literal['clear-snapshots', 'forget-registration', 'delete-map'] = 'clear-snapshots'
    model_config = {'extra': 'forbid'}


class CleanupConfirmation(BaseModel):
    """
    Require an explicit boolean confirmation on the exact preview plan.
    """
    confirm: StrictBool
    model_config = {'extra': 'forbid'}

    @model_validator(mode='after')
    def confirmed(self):
        """
        Reject false/implicit confirmation without starting any map mutation.

        Returns:
            This validated body only when confirm is exactlyTrue.
        Raises:
            ValueError: User confirmation is absent or false.
        """
        if self.confirm is not True:
            raise ValueError('Explicit confirm:true is required')
        return self


def register_map_cleanup_routes(app, cleanup):
    """
    Register transport handlers for one lifecycle-owned cleanup service.

    Args:
        app: Application FastAPI instance; local HTTP boundary remains authoritative.
        cleanup: Shared MapCleanup service owning plans and durable confirmed operations.
    Returns:
        None; registration neither inventories maps nor executes cleanup.
    """
    @app.exception_handler(CleanupError)
    async def cleanup_error(request, error):
        """
        Serialize safe stable cleanup classifications without internal paths or catalog data.

        Args:
            request: Unused HTTP request context.
            error: Expected CleanupError with fixed code/status/retryability.
        Returns:
            JSON error preserving the same plan's safe retry versus new-preview distinction.
        """
        payload = {'error': str(error), 'code': error.code, 'retryable': error.retryable}
        if error.reason is not None:
            payload['reason'] = error.reason
        return JSONResponse(payload, status_code=error.status)

    @app.get('/api/technical/map-cleanup')
    def pending_cleanup():
        """
        List unfinished confirmed receipts for reload recovery without auto-executing anything.

        Returns:
            Bounded pending-operation catalog with an explicit truncation indicator.
        """
        return cleanup.pending()

    @app.post('/api/technical/map-cleanup/preview')
    def preview_cleanup(body: CleanupPreview):
        """
        Preview only explicitly selected existing map IDs.

        Args:
            body: One to50 kind/id selections and an explicit action; default clears owned snapshots.
        Returns:
            Expiring opaque plan with accurate target logical sizes and preserved scopes.
        Raises:
            CleanupError: Busy/stale/unsafe/empty/missing selections leave map files unchanged.
        """
        return cleanup.preview([item.model_dump() for item in body.maps], action=body.action)

    @app.get('/api/technical/map-cleanup/{plan_id}')
    def cleanup_status(plan_id: str):
        """
        Resolve the same opaque plan/operation after a lost acknowledgement or restart.

        Args:
            plan_id: Server-issued plan identity; never a path component accepted without validation.
        Returns:
            Public ready/purging/terminal receipt, retaining preview maps and consequences.
        Raises:
            CleanupError: Unknown404 or expired unconfirmed410 plan; no cleanup is triggered.
        """
        return cleanup.status(plan_id)

    @app.post('/api/technical/map-cleanup/{plan_id}/execute')
    def execute_cleanup(plan_id: str, body: CleanupConfirmation):
        """
        Explicitly confirm one unchanged preview, or retry only its own committed quarantine.

        Args:
            plan_id: Exact opaque preview/confirmed-operation identity.
            body: Validated explicit confirm:true; no paths, epochs or targets can be overridden.
        Returns:
            Idempotent public operation receipt; physical purge continues independently.
        Raises:
            CleanupError: Busy/stale/expired/unsafe requests do not clear newer map data.
        """
        return cleanup.execute(plan_id)
