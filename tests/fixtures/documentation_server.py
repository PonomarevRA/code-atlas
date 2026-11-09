"""Настоящий StreamableHTTP MCP для независимых offline интеграционных проверок."""
import asyncio
import json
from pathlib import Path
import sys
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse, RedirectResponse

port = int(sys.argv[1])
log_path = Path(sys.argv[2])
server = FastMCP('QA Documentation', host='127.0.0.1', port=port, log_level='ERROR')


def record(name, arguments):
    """Записывает фактический tools/call, чтобы обнаружить лишние/опасные вызовы."""
    with log_path.open('a') as stream:
        stream.write(json.dumps({'tool': name, 'arguments': arguments}) + '\n')


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def search_docs(query: str, namespace: str, limit: int = 3) -> dict:
    """Ищет тестовую документацию по ключевым словам в заданном разделе."""
    record('search_docs', {'query': query, 'namespace': namespace, 'limit': limit})
    if query == 'large':
        return {'documents': [{'title': f'Doc {index}', 'text': 'x' * 2000} for index in range(30)]}
    return {'documents': [{'title': f'Documentation: {query}', 'text': f'{namespace}: local reference for {query}', 'url': 'https://docs.example/reference'}, {'title': '<script>unsafe</script>', 'text': '<img onerror=attack()>', 'url': 'javascript:attack()'}]}


@server.tool()
def unknown_search(query: str) -> str:
    """Не имеет read-only annotation; клиент обязан показать неопределённость."""
    record('unknown_search', {'query': query})
    return 'Unknown annotation reference'


@server.tool(annotations=ToolAnnotations(readOnlyHint=False))
def mutate_docs(query: str) -> str:
    """Не должен вызываться через поиск документации."""
    record('mutate_docs', {'query': query})
    return 'mutated'


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=True))
def destroy_docs(query: str) -> str:
    """Опасная annotation имеет приоритет над readOnlyHint."""
    record('destroy_docs', {'query': query})
    return 'destroyed'


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def slow_search(query: str) -> str:
    """Намеренная задержка проверяет общий deadline клиента."""
    await asyncio.sleep(2)
    return 'late'


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def error_search(query: str) -> str:
    """Ошибка инструмента не должна раскрыть внутренние подробности клиенту."""
    raise ValueError('INTERNAL_SECRET_DO_NOT_REFLECT')


@server.custom_route('/health', methods=['GET'])
async def health(request):
    """Позволяет fixture дождаться готовности без tools/call."""
    return JSONResponse({'ready': True})


@server.custom_route('/redirect', methods=['POST','GET','DELETE'])
async def redirect(request):
    """Локальный redirect запрещён даже если конечный host тот же."""
    return RedirectResponse(f'http://127.0.0.1:{port}/redirect-target', status_code=307)


@server.custom_route('/redirect-target', methods=['POST','GET','DELETE'])
async def redirect_target(request):
    """Попадание сюда доказывает нарушение запрета перенаправлений."""
    record('REDIRECT_TARGET_REACHED', {})
    return JSONResponse({'unexpected': True})


@server.custom_route('/redirect-external', methods=['POST','GET','DELETE'])
async def redirect_external(request):
    """Внешний адрес служит только запрещённой целью redirect, не запросом fixture."""
    return RedirectResponse('https://outside.invalid/mcp', status_code=307)


if __name__ == '__main__':
    server.run(transport='streamable-http')
