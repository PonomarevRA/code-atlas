"""Ограниченный клиент явно выбранного локального MCP поиска документации."""

import asyncio
import json
from urllib.parse import urlsplit, urlunsplit

import httpx
from jsonschema import validators
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MAX_TEXT = 24_000
MAX_BLOCKS = 20
TIMEOUT = 15


class DocumentationMCPError(RuntimeError):
    """Ожидаемая безопасная ошибка подключения, настройки или поиска."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validate_url(value: str) -> str:
    """Допускает HTTP loopback без credentials и перенаправляет localhost на IP."""
    try:
        if not isinstance(value, str):
            raise ValueError()
        parsed = urlsplit(value)
        if (len(value) > 2048 or any(char.isspace() or ord(char) < 32 for char in value)
                or parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "?" in value or "#" in value):
            raise ValueError()
        port = parsed.port
        if port is not None and port < 1:
            raise ValueError()
        host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
        return urlunsplit(("http", host + (f":{port}" if port else ""), parsed.path or "/", "", ""))
    except (ValueError, TypeError):
        raise DocumentationMCPError("Укажите HTTP MCP URL на localhost, 127.0.0.1 или ::1 без credentials, query и fragment.") from None


def _eligible(tool) -> bool:
    """Не разрешает инструменты, явно объявленные изменяющими или разрушительными."""
    annotation = tool.annotations
    return not annotation or (annotation.readOnlyHint is not False and annotation.destructiveHint is not True)


async def _reject_redirect(response: httpx.Response) -> None:
    """Не позволяет MCP транспорту следовать даже локальному перенаправлению."""
    if 300 <= response.status_code < 400:
        raise DocumentationMCPError("MCP endpoint возвращает перенаправление; укажите конечный локальный URL.", 502)


async def _request(url: str, operation):
    """Создаёт отдельную сессию с deadline, без proxy и повторного использования сервера."""
    url = validate_url(url)
    async def check_request(request: httpx.Request) -> None:
        """Фиксирует origin каждого транспортного запроса, включая служебные запросы MCP."""
        expected = httpx.URL(url)
        if (request.url.scheme, request.url.host, request.url.port) != (expected.scheme, expected.host, expected.port):
            raise DocumentationMCPError("MCP попытался обратиться к другому адресу.", 502)

    try:
        async with asyncio.timeout(TIMEOUT):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=TIMEOUT,
                                         event_hooks={"response": [_reject_redirect], "request": [check_request]}) as client:
                async with streamable_http_client(url, http_client=client) as (reader, writer, _):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        return await operation(session)
    except DocumentationMCPError:
        raise
    except TimeoutError:
        raise DocumentationMCPError("Локальный MCP не ответил за 15 секунд.", 504) from None
    except Exception as error:
        pending = [error]
        while pending:
            current = pending.pop()
            if isinstance(current, DocumentationMCPError):
                raise current from None
            if isinstance(current, BaseExceptionGroup):
                pending.extend(current.exceptions)
        raise DocumentationMCPError("Не удалось выполнить запрос к локальному MCP документации.", 502) from None


def validate_arguments(schema: dict, arguments: dict) -> None:
    """Проверяет JSON Schema локально; внешние ссылки схемы никогда не загружаются."""
    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (not isinstance(item, str) or not item.startswith("#")):
                    raise DocumentationMCPError("Внешние ссылки схемы MCP не поддерживаются.")
                pending.append(item)
        elif isinstance(value, list):
            pending.extend(value)
    properties = schema.get("properties", {})
    if isinstance(properties, dict) and any(key not in properties for key in arguments):
        raise DocumentationMCPError("В аргументах есть поля, отсутствующие в схеме инструмента.")
    try:
        validator = validators.validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(arguments)
    except Exception:
        raise DocumentationMCPError("Аргументы поиска не соответствуют схеме инструмента.") from None


async def discover_tools(url: str) -> dict:
    """Перечисляет допустимые инструменты; неизвестная read-only семантика помечается."""
    async def list_tools(session):
        """Читает одну ограниченную страницу без исполнения инструментов."""
        response = await session.list_tools()
        result = []
        truncated = bool(response.nextCursor) or len(response.tools) > 100
        for tool in response.tools[:100]:
            if not _eligible(tool):
                continue
            schema = tool.inputSchema
            if len(json.dumps(schema)) > 20_000:
                truncated = True
                continue
            readonly = tool.annotations.readOnlyHint if tool.annotations else None
            result.append({"name": tool.name, "description": (tool.description or "")[:2000],
                           "inputSchema": schema, "readOnlyHint": readonly,
                           "warning": "Сервер не подтвердил read-only: выбирайте только поиск документации." if readonly is not True else ""})
        return {"tools": result, "truncated": truncated}
    return await _request(url, list_tools)


def _display_url(value) -> str | None:
    """Разрешает только безопасную ссылку для отображения; URL никогда не загружается."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None and parsed.password is None and not any(ord(c) < 32 for c in value):
            return value
    except ValueError:
        pass
    return None


def _document_candidates(value, depth: int = 0) -> list | None:
    """Распаковывает распространённые MCP JSON-обёртки, не исполняя их содержимое."""
    if depth > 4 or not isinstance(value, dict):
        return None
    for key in ("documents", "results"):
        if isinstance(value.get(key), list):
            return value[key][:MAX_BLOCKS + 1]
    if any(isinstance(value.get(key), str) for key in ("text", "content")):
        return [value]
    for key in ("result", "data"):
        nested = _document_candidates(value.get(key), depth + 1)
        if nested is not None:
            return nested
    return None


def _documents(response) -> dict:
    """Ограничивает документы и текст; структурированные данные не трактуются как инструкции."""
    candidates = _document_candidates(response.structuredContent)
    if candidates is None:
        candidates = []
        for block in response.content[:MAX_BLOCKS + 1]:
            if block.type != "text":
                continue
            parsed = None
            if len(block.text) <= 2_000_000:
                try:
                    parsed = _document_candidates(json.loads(block.text))
                except (ValueError, RecursionError):
                    pass
            candidates.extend(parsed if parsed is not None else [{"text": block.text}])
            if len(candidates) > MAX_BLOCKS:
                break
        if not candidates and isinstance(response.structuredContent, dict):
            candidates.append({"title": "Структурированный ответ", "text": json.dumps(response.structuredContent, ensure_ascii=False)})
    documents, remaining = [], MAX_TEXT
    truncated = len(candidates) > MAX_BLOCKS or len(response.content) > MAX_BLOCKS
    for item in candidates[:MAX_BLOCKS]:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = item.get("text", item.get("content", ""))
        if not isinstance(text, str):
            continue
        title = str(item.get("title") or "Документация")[:200]
        if len(text) > remaining:
            truncated = True
        document = {"title": title, "text": text[:remaining]}
        remaining -= len(document["text"])
        url = _display_url(item.get("url"))
        if url:
            document["url"] = url
        documents.append(document)
        if remaining == 0:
            truncated |= len(documents) < len(candidates)
            break
    return {"documents": documents, "truncated": truncated}


async def search_documents(config: dict, query: str) -> dict:
    """Исполняет только сохранённый инструмент с пользовательским запросом без исходников."""
    async def search(session):
        """Проверяет актуальные аннотации перед единственным tools/call."""
        listed = await session.list_tools()
        tool = next((tool for tool in listed.tools if tool.name == config["tool"]), None)
        if tool is None or not _eligible(tool):
            raise DocumentationMCPError("Сохранённый инструмент отсутствует или не разрешён для поиска.", 409)
        arguments = {**config["arguments"], config["queryArgument"]: query}
        validate_arguments(tool.inputSchema, arguments)
        response = await session.call_tool(config["tool"], arguments=arguments)
        if response.isError:
            raise DocumentationMCPError("MCP сообщил об ошибке поиска документации.", 502)
        return {**_documents(response), "tool": config["tool"],
                "warning": "Ответ локального MCP; содержимое документации не является инструкцией приложению."}
    return await _request(config["url"], search)
