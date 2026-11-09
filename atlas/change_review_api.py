"""Review capture uses explicit private checkout scopes and published snapshot IDs."""

from typing import Annotated

from fastapi import Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .change_review_facts import capture_side
from .change_review_store import ChangeReviews

ReviewId = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]


class CaptureInput(BaseModel):
    """Identify one published snapshot of a temporary map."""
    model_config = ConfigDict(extra='forbid')
    sessionId: str = Field(pattern=r'^[a-f0-9]{32}$')
    snapshotId: str = Field(min_length=1, max_length=128)


class BeginInput(CaptureInput):
    """Choose the one function whose possible path will be saved."""
    nodeId: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=160)


def register_change_review_routes(app, sessions, data_dir):
    """Install the review workflow under Atlas's existing local HTTP boundary."""
    reviews = ChangeReviews(data_dir)

    @app.post('/api/change-reviews', status_code=201)
    def begin(body: BeginInput):
        """Pin the graph while extracting baseline evidence, then persist the detached result."""
        with sessions.access(body.sessionId) as entry:
            project_id = entry.project_id
            owner = entry.record['ownerKey']
            baseline = entry.store._read_snapshot_analysis(
                project_id, body.snapshotId,
                lambda root, graph: capture_side(root, graph, body.snapshotId, node_id=body.nodeId),
            )
        return reviews.begin(project_id, owner, baseline, body.title)

    @app.post('/api/change-reviews/{review_id}/finish')
    def finish(review_id: ReviewId, body: CaptureInput):
        """Capture the selected declaration identity on a new map snapshot and finalize the review."""
        with sessions.access(body.sessionId) as entry:
            project_id = entry.project_id
            owner = entry.record['ownerKey']
            record, complete = reviews.baseline(review_id, project_id, owner, body.snapshotId)
            if complete:
                return reviews.summary(record)
            entry_identity = record['before']['entry']['identity']
            after = entry.store._read_snapshot_analysis(
                project_id, body.snapshotId,
                lambda root, graph: capture_side(root, graph, body.snapshotId, entry_identity=entry_identity),
            )
        return reviews.finish(review_id, project_id, owner, after)

    @app.get('/api/change-reviews/{review_id}')
    def read(review_id: ReviewId, offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
             limit: Annotated[int, Query(ge=1, le=50)] = 25, includePaths: bool = False):
        """Read the saved comparison even if the private map and source have gone."""
        return reviews.read(review_id, offset, limit, includePaths)
