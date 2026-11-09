"""Loopback HTTP-граница и фактический лимит тела до запуска обработчиков API."""

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class LocalHTTPBoundary:
    """Проверяет Host/Origin и ограничивает тело независимо от Content-Length."""

    def __init__(self, app):
        """Оборачивает ASGI-приложение без изменения его маршрутов и lifespan."""
        self.app = app

    async def __call__(self, scope, receive, send):
        """Буферизует только ограниченный POST; chunked не обходит проверку размера."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request = Request(scope)

        async def secured_send(message):
            """Добавляет одинаковые защитные заголовки успешным и отклонённым ответам."""
            if message["type"] == "http.response.start":
                outgoing = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    outgoing[name] = value
            await send(message)

        async def reject(message: str, status: int):
            """Отклоняет запрос, не передавая его тело или параметры обработчику."""
            await JSONResponse({"error": message}, status_code=status)(scope, receive, secured_send)

        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            await reject("Разрешён только локальный Host", 403)
            return
        origin = headers.get("origin")
        expected_origin = f"{request.url.scheme}://{headers.get('host', '')}"
        if origin and origin != expected_origin:
            await reject("Запрос с другого сайта запрещён", 403)
            return
        if headers.get("sec-fetch-site") == "cross-site":
            await reject("Запрос с другого сайта запрещён", 403)
            return
        if scope["method"] != "POST":
            await self.app(scope, receive, secured_send)
            return
        if headers.get("content-type", "").split(";")[0].strip() != "application/json":
            await reject("Ожидается application/json", 415)
            return
        maximum = 262_144 if scope["path"] == "/api/views" else 16_384
        if scope["path"].startswith("/api/projects/") and scope["path"].endswith("/code-health/plan"):
            maximum = 26 * 1024 * 1024  # JSON escaping can expand the bounded 4 MiB source budget sixfold.
        elif "/code-health" in scope["path"] and scope["path"].startswith("/api/projects/"):
            maximum = 262_144
        if scope['path'].startswith('/api/checkout-sessions/'):
            if scope['path'].endswith('/plan'):
                maximum = 26 * 1024 * 1024  # Same bounded HealthPlanQuery full-draft budget.
            elif scope['path'].endswith(('/health', '/compare')):
                maximum = 262_144
        if scope["path"] == "/api/browser-sessions":
            maximum = 16 * 1024 * 1024  # Only bounded capture ingestion accepts source payloads.
        if scope["path"] == "/api/map-export":
            maximum = 32_000_000
        declared_length = headers.get("content-length", "0")
        if (len(declared_length) > 20 or not declared_length.isascii()
                or not declared_length.isdigit() or int(declared_length) > maximum):
            await reject("Слишком большой запрос", 413)
            return
        chunks, received = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            received += len(body)
            if received > maximum:
                await reject("Слишком большой запрос", 413)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        payload = b"".join(chunks)
        delivered = False

        async def buffered_receive():
            """Передаёт проверенное тело ровно один раз, сохраняя дальнейший disconnect."""
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, secured_send)
