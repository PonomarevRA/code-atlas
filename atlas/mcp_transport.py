"""Bounded loopback HTTP transport shared for one MCP server lifespan."""

from contextlib import asynccontextmanager
import json
import os
from uuid import uuid4

import httpx
from .browser_errors import BrowserSessionError

MAX_HTTP_RESPONSE_BYTES = 16 * 1024 * 1024


class CheckoutBuildPending(ValueError):
    """Bounded building metadata from an exact temporary graph-search request."""

    def __init__(self, details):
        """Preserve the server's session and build IDs for an actionable MCP result."""
        super().__init__('The temporary checkout map is building.')
        self.details = details


def _checkout_build_pending(method, path, body, status, content):
    """Recognize only an unpinned checkout-search conflict with the exact pending schema."""
    parts = path.split('/')
    if (status != 409 or method != 'POST' or not body or body.get('snapshotId') is not None
            or len(parts) != 5 or parts[1:3] != ['api', 'checkout-sessions'] or parts[4] != 'search'):
        return None
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if (not isinstance(payload, dict) or payload.get('code') != 'CHECKOUT_MAP_BUILDING'
            or payload.get('state') != 'building' or payload.get('checkoutSessionId') != parts[3]
            or payload.get('buildStatus') not in ('queued', 'running')
            or type(payload.get('retryAfterMs')) is not int or not 1 <= payload['retryAfterMs'] <= 30_000):
        return None
    for key in ('projectId', 'jobId'):
        if not isinstance(payload.get(key), str) or not 1 <= len(payload[key]) <= 128:
            return None
    return {key: payload[key] for key in ('code', 'state', 'checkoutSessionId', 'projectId',
                                          'buildStatus', 'jobId', 'retryAfterMs')}


def _error_message(content: bytes) -> str:
    """Expose short field-level validation guidance without echoing rejected source/input values."""
    fallback = "проверьте снимок и выбранные узлы"
    try:
        error = json.loads(content)
    except ValueError:
        return fallback
    if not isinstance(error, dict):
        return fallback
    detail = error.get("error") or error.get("detail")
    if isinstance(detail, str):
        return detail[:400]
    if isinstance(detail, list):
        messages = []
        for item in detail[:4]:
            if not isinstance(item, dict) or not isinstance(item.get("msg"), str):
                continue
            location = item.get("loc")
            field = ".".join(str(part)[:80] for part in location[:6]) if isinstance(location, list) else "request"
            messages.append(f"{field}: {item['msg'][:160]}")
        if messages:
            return "; ".join(messages)[:400]
    return fallback


class LoopbackTransport:
    """Reuse connections during stdio service; direct tool calls use a closed temporary client."""

    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"
        self.client = None
        self.activity_session = uuid4().hex
        configured_client = os.environ.get("ATLAS_MCP_CLIENT_KIND", "unknown")
        self.activity_client = configured_client if configured_client in {"codex", "cursor", "claude"} else "unknown"

    def _new_client(self, timeout: float = 30):
        """Keep proxy/redirect isolation and bound pooled sockets independently of request timeouts."""
        return httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout,
                                 limits=httpx.Limits(max_connections=16, max_keepalive_connections=8,
                                                     keepalive_expiry=15))

    @asynccontextmanager
    async def lifespan(self, server):
        """Own all pooled sockets until the MCP server shuts down or startup fails."""
        async with self._new_client() as client:
            self.client = client
            try:
                yield
            finally:
                self.client = None

    async def request(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
        """Read bounded decoded bytes from the fixed origin with an explicit per-call timeout."""
        try:
            if self.client is not None:
                return await self._request(self.client, method, path, body, timeout)
            async with self._new_client(timeout) as client:
                return await self._request(client, method, path, body, timeout)
        except httpx.TimeoutException:
            if path.startswith('/api/browser-sessions'):
                raise BrowserSessionError('Browser request timed out; capture may still complete', 504) from None
            raise ValueError("Запрос Atlas не успел завершиться. Сервер может продолжать анализ; повторите запрос позже.") from None
        except httpx.HTTPError:
            if path.startswith('/api/browser-sessions'):
                raise BrowserSessionError('Local Atlas is unavailable', 502) from None
            raise ValueError("Локальный Atlas недоступен. Запустите приложение на выбранном порту.") from None

    async def _request(self, client, method, path, body, timeout):
        """Limit collected decoded bytes before JSON parsing; decoder internals remain owned by httpx."""
        headers = {"X-Atlas-MCP-Session": self.activity_session, "X-Atlas-MCP-Client": self.activity_client}
        async with client.stream(method, self.base + path, json=body, timeout=timeout, headers=headers) as response:
            if response.is_redirect:
                raise ValueError("Перенаправления Atlas не разрешены")
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(content) + len(chunk) > MAX_HTTP_RESPONSE_BYTES:
                    raise ValueError("Ответ Atlas превышает допустимый размер")
                content.extend(chunk)
            if response.status_code >= 400:
                if path.startswith('/api/browser-sessions'):
                    # Never parse page-dependent error text or reflect remote recovery fields.
                    raise BrowserSessionError('Browser request rejected by Atlas', response.status_code)
                pending = _checkout_build_pending(method, path, body, response.status_code, content)
                if pending is not None:
                    raise CheckoutBuildPending(pending)
                message = _error_message(content)
                raise ValueError(f"Atlas отклонил запрос (HTTP {response.status_code}): {message}")
            try:
                result = json.loads(content)
            except ValueError:
                raise ValueError("Atlas вернул некорректный JSON. Проверьте порт и версию локального приложения.") from None
            if not isinstance(result, dict):
                raise ValueError("Atlas вернул неожиданный формат. Проверьте версию локального приложения и MCP.")
            return result
