"""Наблюдает реальные MCP HTTP операции один раз, без изменения их результата."""

import asyncio
import json
import time
from starlette.datastructures import Headers

from .events import activity_event, operation_context
from .summaries import request_summary, response_summary


class ActivityObserver:
    """Обрабатывает только явно помеченный MCP transport; UI не изображается агентом."""

    def __init__(self, app, journal):
        """Подключает observer внутри существующей HTTP Host/Origin/body границы."""
        self.app, self.journal = app, journal

    def _record(self, event):
        """Изолирует неожиданный сбой журнала от результата и исключения основной операции."""
        try:
            self.journal.submit(event)
        except Exception:
            # No recursive logging or exception payload enters the failing journal.
            self.journal.dropped["invalid_event"] += 1

    async def __call__(self, scope, receive, send):
        """Фиксирует started/finished и bounded response summary; исключения проходят без замены."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        session = headers.get("x-atlas-mcp-session", "")
        observe = (scope["type"] == "http" and scope["path"].startswith("/api/")
                   and not scope["path"].startswith("/api/agent-activity")
                   and not scope["path"].startswith("/api/browser-sessions")
                   and len(session) == 32 and all(char in "0123456789abcdef" for char in session))
        if not observe:
            await self.app(scope, receive, send)
            return
        client = headers.get("x-atlas-mcp-client", "unknown")
        if client not in {"codex", "cursor", "claude", "unknown"}:
            client = "unknown"
        payload = None
        first = None
        if scope["method"] == "POST":
            first = await receive()
            raw = first.get("body", b"")
            if len(raw) <= 16384:
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeError):
                    pass
        context = operation_context(scope, payload)
        context["request"] = request_summary(scope["path"], context)
        self._record(activity_event(context, session, client, "started"))
        began, status, response, oversized, total_bytes = time.monotonic(), 500, bytearray(), False, 0

        async def replay_receive():
            """Передаёт исходное тело без повторного чтения или изменения размера."""
            nonlocal first
            if first is not None:
                value, first = first, None
                return value
            return await receive()

        async def observed_send(message):
            """Собирает только маленький response для resultCount, не удерживает исходники."""
            nonlocal status, oversized, total_bytes
            if message["type"] == "http.response.start":
                status = message["status"]
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                total_bytes += len(body)
                remaining = max(0, 32768 - len(response))
                response.extend(body[:remaining])
                oversized = total_bytes > 32768
            await send(message)

        outcome, error = "unknown", None
        try:
            await self.app(scope, replay_receive, observed_send)
            outcome = "succeeded" if status < 400 else "failed"
            error = "http_error" if status >= 400 else None
        except asyncio.CancelledError:
            outcome, error = "cancelled", "cancelled"
            raise
        except Exception:
            outcome, error = "failed", "operation_error"
            raise
        finally:
            try:
                summary = response_summary(response, total_bytes, oversized, status, error)
            except Exception:
                summary = {"summary": f"HTTP {status}; обзор недоступен", "truncated": True, "errorCode": error}
            self._record(activity_event(context, session, client, "finished", outcome=outcome,
                                        durationMs=max(0, (time.monotonic() - began) * 1000),
                                        resultCount=summary.get("resultCount"), errorCode=error, response=summary))
