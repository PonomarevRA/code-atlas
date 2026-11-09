"""Bounded transport contracts for local research mods."""
from pydantic import BaseModel, Field, model_validator


class ResearchError(ValueError):
    """Expected workflow failure with a safe HTTP message."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class StartResearch(BaseModel):
    """Pin a mod revision, project snapshot and bounded question/selected symbol."""
    projectId: str = Field(min_length=1, max_length=128)
    snapshotId: str = Field(min_length=1, max_length=128)
    modId: str = Field(pattern=r'^[a-z][a-z0-9-]{0,47}$')
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    workflowId: str = Field(min_length=1, max_length=48)
    nodeId: str = Field(default='', max_length=1000)
    question: str = Field(default='', max_length=2000)
    maxNodes: int = Field(default=6, ge=1, le=12, strict=True)
    maxDepth: int = Field(default=1, ge=0, le=3, strict=True)
    requestKey: str = Field(pattern=r'^[A-Za-z0-9-]{16,80}$')
    model_config = {'extra': 'forbid'}

    @model_validator(mode='after')
    def require_scope(self):
        """A job needs a selected symbol or an actual question, not an unbounded repository scan."""
        self.question = self.question.strip()
        if not self.nodeId and not self.question:
            raise ValueError('Укажите вопрос или выберите символ.')
        return self
