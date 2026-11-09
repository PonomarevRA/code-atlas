"""Bounded file/line anchor and separate directed source dependency contexts."""

from pathlib import Path
from .models import PositionContextError, PositionContextInput

__all__ = ['PositionContextInput', 'PositionContextError', 'build_position_context']


def build_position_context(project_path: Path, graph: dict, body: PositionContextInput) -> dict:
    """Resolve a source position and return a bounded snapshot-bound dependency context.

    Args:
        project_path: Trusted selected checkout root; no files outside it are read.
        graph: Host-captured raw graph for body.snapshotId, never a request-supplied graph.
        body: Strict file/line/depth/direction/budget request.
    Returns:
        atlas-position-dependencies/v1 context with explicit resolution/freshness evidence.
    Raises:
        PositionContextError: Unsafe/missing source, stale expected hash or invalid/budget input.
    """
    from .service import build
    return build(project_path, graph, body)
