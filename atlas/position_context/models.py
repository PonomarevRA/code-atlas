"""Strict public input and errors for source-position dependency context."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

VERSION = 'atlas-position-dependencies/v1'


class PositionContextError(ValueError):
    """Carry a safe HTTP classification without coupling the pure core to transport."""

    def __init__(self, message: str, status: int = 400, code: str = 'position_context_invalid'):
        super().__init__(message)
        self.status, self.code = status, code


class PositionContextInput(BaseModel):
    """Select a snapshot/file/one-based line and two independent directed traversals."""

    model_config = ConfigDict(extra='forbid')
    snapshotId: str = Field(min_length=1, max_length=128)
    file: str = Field(min_length=1, max_length=4096)
    line: int = Field(strict=True, ge=1)
    depth: int = Field(default=2, strict=True, ge=1, le=8)
    direction: Literal['incoming', 'outgoing', 'both'] = 'both'
    maxNodes: int = Field(default=60, strict=True, ge=1, le=120)
    maxEdges: int = Field(default=120, strict=True, ge=1, le=240)
    expectedSourceHash: str | None = Field(default=None, pattern=r'^[a-fA-F0-9]{64}$')
