"""Запросы к настоящему локальному MCP-серверу Graphify через stdio."""

import asyncio
from pathlib import Path

from .graphify_adapter import _offline_environment
from .integrations.graphify.runtime import command as provider_command


class McpError(RuntimeError):
    """MCP-сервер недоступен либо вернул ошибку инструмента."""


def _result_text(result: object) -> str:
    """Извлекает текст MCP, ограничивая размер ответа для браузера."""
    sections = []
    for block in getattr(result, "content", []):
        if getattr(block, "type", "") == "text":
            sections.append(str(block.text))
    text = "\n".join(sections)[:24000]
    if getattr(result, "isError", False):
        raise McpError(text or "MCP-инструмент вернул ошибку.")
    if not text:
        raise McpError("MCP-инструмент вернул пустой ответ.")
    return text


async def _call_tools(graph_path: Path, calls: list[tuple[str, dict]]) -> dict:
    """Инициализирует stdio-сессию и выполняет разрешённые read-only запросы."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise McpError("Не установлены MCP-зависимости graphifyy[mcp].") from exc
    graph_path = graph_path.resolve()
    if not graph_path.is_file():
        raise McpError("Файл графа отсутствует. Сначала постройте карту.")
    command = provider_command("graphify.serve", [str(graph_path)])
    parameters = StdioServerParameters(
        command=command[0],
        args=command[1:],
        cwd=str(graph_path.parent),
        env=_offline_environment(),
    )
    sections = []
    async with asyncio.timeout(30):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments=arguments)
                    sections.append(_result_text(result))
    return {
        "text": "\n\n".join(sections)[:32000],
        "provider": "Graphify MCP",
        "tools": [name for name, _ in calls],
    }


async def describe_node(graph_path: Path, node_id: str) -> dict:
    """Возвращает свойства узла и связи из MCP без обращения к языковой модели."""
    if not node_id.strip():
        raise McpError("Не выбран узел для описания.")
    calls = [
        ("get_node", {"node_id": node_id}),
        ("get_neighbors", {"node_id": node_id, "token_budget": 2000}),
    ]
    return await _call_tools(graph_path, calls)


async def query_graph(graph_path: Path, question: str) -> dict:
    """Ищет связанный контекст по вопросу средствами локального Graphify MCP."""
    if not question.strip() or len(question) > 2000:
        raise McpError("Введите вопрос длиной от 1 до 2000 символов.")
    calls = [("query_graph", {
        "question": question, "mode": "bfs", "depth": 3, "token_budget": 2000,
    })]
    return await _call_tools(graph_path, calls)
