"""Explicit scoped storage cleanup: preview by default, minimum three snapshots."""
from pydantic import BaseModel, Field


class DocumentRetentionRequest(BaseModel):
    """A map selects its project/root artifact scope; dryRun never deletes files."""
    projectId: str | None = Field(default=None, max_length=128)
    dryRun: bool = Field(default=True, strict=True)
    model_config = {'extra': 'forbid'}


def register_document_retention_routes(app, documents):
    """Register independent retention routes without scanning or modifying storage."""
    @app.post('/api/document-maps/{map_id}/retention')
    def retention(map_id: str, body: DocumentRetentionRequest):
        """Preview or collect this map's shared artifact scope, returning scopeMapIds."""
        return documents.retention(map_id, project_id=body.projectId, dry_run=body.dryRun)
