"""Strict transports for opt-in Pareto proposals and host-retained incremental state."""

from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .analysis_contexts import AnalysisContextError
from .module_search import ModuleSearchError
from .module_updates import ModuleUpdateError
from .module_request import run_module_request


class ModuleScope(BaseModel):
    """Select captured source units; caller ownership declarations stay explicit."""
    model_config = ConfigDict(extra='forbid')
    snapshotId: str = Field(min_length=1, max_length=128)
    level: Literal['folder', 'class'] = 'folder'
    folders: list[str] | None = Field(default=None, max_length=32)
    ownership: dict[str, str] | None = Field(default=None, max_length=10000)


class ModuleConstraints(BaseModel):
    """Hard membership/size constraints, validated again against actual captured unit IDs."""
    model_config = ConfigDict(extra='forbid')
    mustLink: list[tuple[str, str]] = Field(default_factory=list, max_length=16384)
    cannotLink: list[tuple[str, str]] = Field(default_factory=list, max_length=16384)
    minGroupUnits: int = Field(default=1, strict=True, ge=1, le=128)
    maxGroupUnits: int = Field(default=128, strict=True, ge=1, le=128)
    minGroups: int = Field(default=1, strict=True, ge=1, le=128)
    maxGroups: int = Field(default=128, strict=True, ge=1, le=128)


class ModuleSearchInput(ModuleScope):
    """Bound a reproducible equal-budget search without accepting a caller graph."""
    currentPartition: list[list[str]] | None = Field(default=None, max_length=128)
    constraints: ModuleConstraints | None = None
    seed: int = Field(default=42, strict=True, ge=0, le=4294967295)
    evaluationsPerMethod: int = Field(default=64, strict=True, ge=1, le=256)
    maxProposals: int = Field(default=8, strict=True, ge=1, le=16)


class ModuleStateReference(BaseModel):
    """Opaque host-owned context; metadata is checked against the actual residency scope."""
    model_config = ConfigDict(extra='forbid')
    contextId: str = Field(pattern=r'^[a-f0-9]{32}$')
    snapshotId: str = Field(min_length=1, max_length=128)
    settings: str = Field(min_length=1, max_length=128)
    idleTimeoutSeconds: int | None = Field(default=None, ge=1, le=86400, strict=True)


class ModuleIncrementalInput(ModuleScope):
    """Read a prior host state or initialize a new one with an explicit candidate budget."""
    initialPartition: list[list[str]] | None = Field(default=None, max_length=200)
    stateRef: ModuleStateReference | None = None
    maxEvaluations: int = Field(default=4096, strict=True, ge=1, le=16384)


def register_module_proposal_routes(app, service):
    """
    Add explicit source-bound proposal APIs without changing normal graph loads.

    Args:
        app: Existing FastAPI composition owner with StoreError and local-boundary handlers.
        service: ModuleProposalService sharing source admission and AnalysisContexts ownership.
    Returns:
        None; registration does not compute, retain state or alter default grouping.
    """
    async def proposal_error(request, error):
        """Expose stable pure-engine/context classifications without request bodies or traces."""
        return JSONResponse({'error': str(error), 'code': getattr(error, 'code', 'analysis_context_error')},
                            status_code=error.status)
    for error_type in (ModuleSearchError, ModuleUpdateError, AnalysisContextError):
        app.add_exception_handler(error_type, proposal_error)

    @app.post('/api/projects/{project_id}/module-search')
    async def search(project_id: str, body: ModuleSearchInput, request: Request):
        """Search read-only proposals for one exact published snapshot."""
        # The pure engine derives group-count defaults from actual captured N.
        # Expanding the DTO's schema maximum would reject valid small scopes.
        return await run_module_request(request, lambda cancelled:
            service.search(project_id, body.model_dump(exclude_unset=True), cancel_event=cancelled))

    @app.post('/api/projects/{project_id}/module-updates')
    async def incremental(project_id: str, body: ModuleIncrementalInput, request: Request):
        """Prepare/update a private analysis state; unknown/expired state never becomes caller facts."""
        return await run_module_request(request, lambda cancelled:
            service.incremental(project_id, body.model_dump(), cancel_event=cancelled),
            discard=lambda result: service.discard(project_id, result))
