"""Same-origin история и bounded WebSocket adapter поверх durable journal."""

import asyncio
import ipaddress
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Query, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

MAX_FRAME_BYTES = 65536


def allowed_socket(socket):
    """До accept проверяет peer, loopback Host и точный browser Origin; native bypass отсутствует."""
    host = socket.headers.get("host", "")
    try:
        hostname = urlsplit("http://" + host).hostname
        peer = ipaddress.ip_address(socket.client.host)
    except (ValueError, AttributeError):
        return False
    scheme = "https" if socket.url.scheme == "wss" else "http"
    return (peer.is_loopback and hostname in {"localhost", "127.0.0.1", "::1"}
            and socket.headers.get("origin") == f"{scheme}://{host}"
            and socket.headers.get("sec-fetch-site") != "cross-site")


def project_event(event, store):
    """Создаёт browser DTO из allowlist; удалённый project не раскрывает сохранённые target IDs."""
    fields = {"schemaVersion", "eventId", "journalEpoch", "sequence", "occurredAt", "recordedAt",
              "application", "source", "clientKind", "sessionId", "agentId", "identityQuality",
              "operationId", "parentOperationId", "operation", "phase", "outcome", "durationMs",
              "resultCount", "errorCode", "query", "target", "request", "response"}
    value = {key: event[key] for key in fields if key in event}
    value["target"] = dict(event.get("target") or {})
    value["query"] = dict(event.get("query") or {})
    target = value.get("target") or {}
    project = target.get("projectId")
    if project:
        with store.lock:
            available = project in store.projects
        if not available:
            value["target"] = {"kind": "code", "status": "unavailable"}
    return value


def cursor_sequence(cursor, epoch, oldest, latest):
    """Проверяет строковый cursor и replay window без потери точности JavaScript."""
    if not isinstance(cursor, dict) or set(cursor) != {"journalEpoch", "sequence"}:
        raise ValueError("invalid_cursor")
    raw = cursor.get("sequence")
    if not isinstance(raw, str) or not re.fullmatch(r"0|[1-9][0-9]{0,19}", raw):
        raise ValueError("invalid_cursor")
    sequence = int(raw)
    if cursor.get("journalEpoch") != epoch or sequence < oldest - 1 or sequence > latest:
        raise ValueError("cursor_outside_retained_window")
    return sequence


async def receive_control(socket):
    """Читает только JSON control до 1 КиБ; бинарные сообщения и массивы отклоняются."""
    message = await socket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024:
        raise ValueError("invalid_control")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid_control")
    return value


async def acknowledged_send(socket, frame):
    """Один outstanding batch: socket send и reducer ACK имеют отдельные bounded deadlines."""
    await asyncio.wait_for(socket.send_json(frame), timeout=5)
    reply = await asyncio.wait_for(receive_control(socket), timeout=5)
    if reply.get("type") != "ack" or reply.get("messageId") != frame["messageId"] or reply.get("cursor") != frame["cursor"]:
        raise ValueError("invalid_ack")


def make_frame(kind, rows, status, store, sequence=None):
    """Ограничивает целый JSON envelope 64 КиБ, возвращает последний включённый sequence."""
    frame = {"type": kind, "messageId": uuid4().hex, "journalEpoch": status["journalEpoch"],
             "cursor": {"journalEpoch": status["journalEpoch"], "sequence": str(sequence if sequence is not None else status["latestSequence"])},
             "events": [], "health": status["health"], "omittedCount": 0}
    size = len(json.dumps(frame, ensure_ascii=False).encode("utf-8")) + 64
    candidates = reversed(rows) if kind == "snapshot" else rows
    for row in candidates:
        projected = project_event(row, store)
        row_size = len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) + 2
        if size + row_size > MAX_FRAME_BYTES:
            break
        size += row_size
        frame["events"].append(projected)
        if kind == "events":
            frame["cursor"]["sequence"] = row["sequence"]
    if kind == "snapshot":
        frame["events"].reverse()
    frame["omittedCount"] = len(rows) - len(frame["events"])
    return frame


async def stream_activity(socket, journal, store, project_id):
    """Согласует snapshot as-of H или replay, затем отправляет только durable хвост с ACK."""
    if not allowed_socket(socket) or journal.disk is None or len(journal.watchers) >= 16:
        await socket.close(code=1008)
        return
    await socket.accept()
    subscription = journal.subscribe()
    try:
        control = await asyncio.wait_for(receive_control(socket), 5)
        if control.get("type") != "subscribe" or set(control) - {"type", "cursor"}:
            raise ValueError("invalid_subscribe")
        with journal.disk.lock:
            status = journal.status()
            oldest, latest = journal.disk.bounds()
            if control.get("cursor") is not None:
                sequence = cursor_sequence(control["cursor"], journal.disk.epoch, oldest, latest)
            else:
                rows = journal.disk.read(limit=128, project_id=project_id)
                frame = make_frame("snapshot", rows, status, store)
                frame["omittedCount"] = max(0, latest - oldest + 1 - len(frame["events"]))
                sequence = latest
        epoch = status["journalEpoch"]
        if control.get("cursor") is None:
            await acknowledged_send(socket, frame)
        else:
            # An empty events batch acknowledges successful resume even with no new records.
            await acknowledged_send(socket, make_frame("events", [], status, store, sequence))
        while True:
            subscription[1].clear()
            with journal.disk.lock:
                status = journal.status()
                oldest, latest = journal.disk.bounds()
                if epoch != status["journalEpoch"] or sequence < oldest - 1:
                    raise ValueError("cursor_outside_retained_window")
                rows = journal.disk.read(after=sequence, limit=128, project_id=project_id)
                if rows:
                    frame = make_frame("events", rows, status, store, sequence)
                elif latest > sequence:
                    # Filtered-out records still advance the global commit cursor safely.
                    sequence = latest
                    frame = make_frame("events", [], status, store, sequence)
                else:
                    frame = None
            if frame is not None:
                await acknowledged_send(socket, frame)
                sequence = int(frame["cursor"]["sequence"])
                continue
            # Also read control to notice a disconnected idle client without journal polling.
            wake = asyncio.create_task(subscription[1].wait())
            incoming = asyncio.create_task(receive_control(socket))
            try:
                done, _ = await asyncio.wait({wake, incoming}, return_when=asyncio.FIRST_COMPLETED)
                if incoming in done:
                    incoming.result()
                    raise ValueError("unexpected_control")
            finally:
                for task in (wake, incoming):
                    task.cancel()
                # Consume disconnect exceptions even if the ASGI parent is cancelled on close.
                await asyncio.gather(wake, incoming, return_exceptions=True)
    except (ValueError, asyncio.TimeoutError) as error:
        try:
            await asyncio.wait_for(socket.send_json({"type": "resetRequired", "reason": str(error)[:64] or "consumer_timeout",
                                                    "journalEpoch": journal.status()["journalEpoch"]}), 1)
            await socket.close(code=4009)
        except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError):
            pass
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        journal.unsubscribe(subscription)


def register_activity_routes(app, journal, store):
    """Добавляет историю/status/clear и stream без изменения существующего MCP schema."""
    from .http_api import register_http_activity_routes
    register_http_activity_routes(app, journal, store, project_event)

    @app.get("/api/agent-activity/status")
    def activity_status():
        """Показывает effective service settings и состояние durable writer."""
        return journal.status()

    @app.get("/api/agent-activity/history")
    def activity_history(projectId: str | None = Query(None, max_length=128),
                         sessionId: str | None = Query(None, max_length=128),
                         operation: str | None = Query(None, max_length=128),
                         q: str | None = Query(None, max_length=512),
                         beforeSequence: str | None = Query(None, pattern=r"^(0|[1-9][0-9]{0,19})$"),
                         limit: int = Query(100, ge=1, le=100)):
        """Возвращает bounded страницу в порядке commit с фильтрами без хранения browser history."""
        if journal.disk is None:
            return JSONResponse({"error": "activity_unavailable"}, status_code=503)
        with journal.disk.lock:
            status = journal.status()
            rows = journal.disk.read(before=int(beforeSequence) if beforeSequence else None, limit=limit + 1,
                                     project_id=projectId, session_id=sessionId, operation=operation, query=q)
            more = len(rows) > limit
            if more:
                rows = rows[-limit:]
            projected, size, omitted = [], 1024, 0
            # Preserve newest records within the same bounded page, including UTF-8 JSON overhead.
            for row in reversed(rows):
                value = project_event(row, store)
                row_size = len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 2
                if size + row_size > 262144:
                    omitted += 1
                    more = True
                    continue
                size += row_size
                projected.append(value)
            projected.reverse()
            return {"events": projected, "cursor": status["cursor"], "omittedCount": omitted,
                    "oldestSequence": status["oldestSequence"], "hasMore": more, "resetRequired": False}

    @app.post("/api/agent-activity/clear")
    def activity_clear():
        """Очищает только activity journal; Host/Origin POST guard сохраняется."""
        try:
            return journal.clear()
        except OSError:
            return JSONResponse({"error": "activity_clear_failed"}, status_code=503)

    @app.websocket("/api/agent-activity/stream")
    async def activity_stream(socket: WebSocket, projectId: str | None = Query(None, max_length=128)):
        """Открывает одну browser subscription для панели и всех маркеров страницы."""
        await stream_activity(socket, journal, store, projectId)
