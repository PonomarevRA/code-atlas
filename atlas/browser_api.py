"""Loopback endpoints for temporary browser graphs; never register captured files as projects."""
from typing import Annotated
from fastapi import Path
from fastapi.responses import JSONResponse

SessionId = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]

from .browser_models import BrowserCreate, BrowserQuery, BrowserNode, BrowserSource, BrowserFinish, BrowserNodeSource, BrowserProperty
from .browser_sessions import BrowserSessionError


def register_browser_routes(app, sessions, *, privacy=None):
    """Expose one isolated service under the existing Host/Origin and JSON boundaries."""
    from .browser_privacy import BrowserPrivacy
    from .browser_privacy_api import register_privacy_routes
    sessions.privacy = privacy if privacy is not None else BrowserPrivacy(app.state.store.data_dir / 'browser-privacy')
    register_privacy_routes(app, sessions)

    def disclosed(identifier, result):
        """Every content-bearing response includes only the same session's generic masking notice."""
        with sessions.lock:
            result['privacy'] = sessions._entry(identifier)['metadata'].get('privacy')
        return result

    @app.exception_handler(BrowserSessionError)
    async def browser_error(request, error):
        """Report stable errors without reflecting captured page data or parser exceptions."""
        return JSONResponse(error.public(), status_code=error.status)

    @app.post('/api/browser-sessions')
    def create(body: BrowserCreate):
        """Import an already captured page; the server performs no browser navigation."""
        return sessions.create(body)

    @app.get('/api/browser-sessions')
    def list_sessions():
        """List live metadata without retaining a second copy of captured sources."""
        return sessions.list()

    @app.get('/api/browser-sessions/{session_id}')
    def overview(session_id: SessionId):
        """Return exact-session metadata and coverage without exposing sources."""
        return sessions.overview(session_id)

    @app.post('/api/browser-sessions/{session_id}/node-source')
    def node_source(session_id: SessionId, body: BrowserNodeSource):
        """Read lexical function source without asking the client to translate byte offsets."""
        return disclosed(session_id, sessions.node_source(session_id, body))

    @app.post('/api/browser-sessions/{session_id}/property')
    def node_property(session_id: SessionId, body: BrowserProperty):
        """Read an exact DOM property omitted from a compact node response."""
        return disclosed(session_id, sessions.property(session_id, body))

    @app.post('/api/browser-sessions/{session_id}/query')
    def query(session_id: SessionId, body: BrowserQuery):
        """Read a bounded text-search page from a live temporary graph."""
        return disclosed(session_id, sessions.query(session_id, body))

    @app.post('/api/browser-sessions/{session_id}/node')
    def node(session_id: SessionId, body: BrowserNode):
        """Read an exact node and a bounded page of its original evidence edges."""
        return disclosed(session_id, sessions.node(session_id, body))

    @app.post('/api/browser-sessions/{session_id}/source')
    def source(session_id: SessionId, body: BrowserSource):
        """Read selected original JS without rebuilding or interpreting it."""
        return disclosed(session_id, sessions.source(session_id, body))

    @app.post('/api/browser-sessions/{session_id}/finish')
    def finish(session_id: SessionId, body: BrowserFinish):
        """Release session contents after completion, cancellation or failure."""
        return sessions.finish(session_id, body.reason)
