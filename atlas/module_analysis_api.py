"""Validated HTTP boundary for explicit read-only module proposals."""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .module_analysis import ModuleAnalysisError
from .module_request import run_module_request


class ModuleAnalysisInput(BaseModel):
    """Pin the source and allowed providers; caller ownership stays labelled as such."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    snapshotId: str = Field(min_length=1, max_length=128)
    level: Literal["folder", "class"] = "folder"
    folders: list[str] | None = Field(default=None, max_length=32)
    ownership: dict[str, str] | None = Field(default=None, max_length=10000)
    algorithms: list[Literal["current", "louvain", "leiden", "sarf-simple-directed"]] = Field(
        default_factory=lambda: ["current", "louvain", "leiden"], min_length=1, max_length=4,
    )
    seed: int = Field(default=42, ge=0, le=4294967295, strict=True)
    resolution: float = Field(default=1.0, gt=0, le=10, strict=True)


def register_module_analysis_routes(app: FastAPI, service) -> None:
    """Register an opt-in computation, separate from normal graph loading."""
    @app.exception_handler(ModuleAnalysisError)
    async def module_analysis_error(request, error):
        """Map engine/admission failures to a stable status and category."""
        return JSONResponse({"error": str(error), "code": error.code}, status_code=error.status)

    @app.post("/api/projects/{project_id}/module-analysis")
    async def analyze_modules(project_id: str, body: ModuleAnalysisInput, request: Request):
        """Compute proposals without applying them to the saved map or repository."""
        return await run_module_request(request, lambda cancelled:
            service.analyze(project_id, body.model_dump(), cancel_event=cancelled))
