"""Authenticated native settings are separate from all browser agent read/capture tools."""
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator
from typing import Literal


class HumanPrivacyChoice(BaseModel):
    """No raw exemption fields, paths, keys or request-time mode overrides."""
    model_config = ConfigDict(extra='forbid', strict=True)
    mode: Literal['strict', 'credentials-only', 'off']
    confirmed: bool

    @field_validator('confirmed')
    @classmethod
    def actual_confirmation(cls, value):
        """Numeric truthiness is never an explicit confirmation."""
        if value is not True:
            raise ValueError('human-confirmation-required')
        return value


def register_privacy_routes(app, sessions):
    """Only fixed status is public; native installation credential protects mode mutation."""
    @app.get('/api/browser-privacy')
    def status():
        """Return mode only, with no installation credentials or captured values."""
        return sessions.privacy.status()

    @app.post('/api/browser-privacy')
    def choose(request: Request, body: HumanPrivacyChoice):
        """Commit a host-authenticated human choice and revoke all previous capture IDs."""
        if not sessions.privacy.authorize(request.headers.get('X-Atlas-Native-Control', '')):
            raise HTTPException(403, 'native-control-required')
        if not sessions.admission.acquire(blocking=False):
            raise HTTPException(503, 'browser-busy')
        try:
            # Existing reads finish before revocation. All subsequent reads must select a new exact ID.
            with sessions.lock:
                sessions.entries.clear()
                try:
                    return sessions.privacy.select_after_human_confirmation(body.mode)
                except Exception:
                    raise HTTPException(503, 'browser-privacy-unavailable') from None
        finally:
            sessions.admission.release()
