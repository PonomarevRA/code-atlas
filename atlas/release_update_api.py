"""Expose the explicit, cache-only end-of-task reminder claim on loopback."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


UPDATE_NOTICE_PATH = '/api/updates/v1/after-task'


class UpdateNoticeInput(BaseModel):
    """Choose the release note language for one end-of-task claim."""

    model_config = ConfigDict(extra='forbid')
    locale: Literal['ru', 'en'] = 'en'


def register_release_update_routes(app, updates):
    """Install a separate operation with no dependency on graph or task readiness."""

    @app.post(UPDATE_NOTICE_PATH)
    def claim_update_notice(body: UpdateNoticeInput):
        """Claim an optional previously cached reminder after the agent's work."""
        return updates.claim(body.locale)
