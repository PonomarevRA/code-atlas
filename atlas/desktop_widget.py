"""Read-only OS widget snapshot from the existing bounded agent activity journal."""

from datetime import datetime, timezone
import json
import math
import re

from fastapi import Request
from .runtime_paths import APP_VERSION


def plain(value):
    """Bound display text and omit common credential assignments from existing summaries."""
    value = value if isinstance(value, str) else ''
    value = re.sub(r'(?i)(token|password|secret|authorization|api[_-]?key)\s*[:=]\s*\S+', r'\1=[redacted]', value)
    return ''.join(char for char in value[:512] if ord(char) >= 32 and not 0xD800 <= ord(char) <= 0xDFFF)


def widget_snapshot(journal, origin):
    """Read at most 32 existing events and publish at most eight completed request summaries."""
    requests = []
    available = journal.disk is not None and not journal.error
    if available:
        try:
            rows = journal.disk.read(limit=32)
            for row in reversed(rows):
                if row.get('phase') not in {'completed', 'failed', 'finish', 'finished'}:
                    continue
                duration = row.get('durationMs')
                if type(duration) not in (float, int) or not math.isfinite(duration) or duration < 0:
                    duration = None
                request, response = row.get('request') or {}, row.get('response') or {}
                requests.append({
                    'timestamp': plain(row.get('occurredAt')), 'operation': plain(row.get('operation')),
                    'actorLabel': plain(row.get('agentId') or row.get('clientKind') or 'Unknown client'),
                    'status': plain(row.get('outcome') or row.get('phase')), 'durationMs': duration,
                    'requestSummary': plain(request.get('summary')),
                    'responseSummary': plain(response.get('summary')),
                })
                if len(json.dumps(requests, ensure_ascii=False).encode('utf-8')) > 60000:
                    requests.pop()
                    break
                if len(requests) == 8:
                    break
        except (OSError, ValueError, KeyError, TypeError):
            available = False
    return {'schemaVersion': 'ag-desktop/widget-v1', 'appId': 'code-atlas', 'applicationVersion': APP_VERSION,
            'capturedAt': datetime.now(timezone.utc).isoformat(), 'status': 'ok' if available else 'degraded',
            'origin': origin, 'requests': requests,
            'source': 'Completed events from local agent activity journal; client labels are not authenticated agent identities.'}


def register_desktop_widget(app, journal):
    """Reuse the application's existing loopback Host/Origin boundary and no-store policy."""
    from desktop_support.activity import install_activity
    install_activity(app, 'code-atlas')
    @app.get('/api/desktop/v1/widget')
    def desktop_widget(request: Request):
        """Return a widget snapshot without executing a model or querying project graphs."""
        return widget_snapshot(journal, str(request.base_url).rstrip('/'))
