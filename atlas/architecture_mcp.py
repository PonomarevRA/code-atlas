"""MCP-прокси архитектурных проверок через общий loopback HTTP-сервис."""
from typing import Annotated, Literal
from urllib.parse import quote, urlencode
from mcp.types import ToolAnnotations
from pydantic import Field
from .architecture_rules import Rules


ARCHITECTURE_TOOLS = ['run_architecture_checks', 'read_architecture_checks', 'read_architecture_evidence', 'read_architecture_source']

SMALL_ID = Annotated[str, Field(min_length=1, max_length=128)]
OFFSET = Annotated[int, Field(strict=True, ge=0, le=100000)]
LIMIT = Annotated[int, Field(strict=True, ge=1, le=20)]


def register_architecture_tools(server, request, selected_project):
    """Проверяет bound project до HTTP; не читает файлы и не воспроизводит алгоритм."""
    def route(project_id, suffix=''):
        return '/api/projects/' + quote(selected_project(project_id), safe='') + '/architecture-checks' + suffix

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    async def run_architecture_checks(snapshotId: SMALL_ID, rules: Rules, projectId: str | None = None) -> dict:
        """Захватить статические зависимости по явным правилам; RAM-результат хранится до часа.

        passed относится только к полной явно выбранной static relation/scope; runtime
        не доказан. violated может сосуществовать с неизвестным покрытием. Ответ — первая
        страница правил; используйте read_architecture_checks для остальных и findings.
        """
        return await request('POST', route(projectId), {'snapshotId': snapshotId, 'rules': rules.model_dump()})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def read_architecture_checks(runId: SMALL_ID, projectId: str | None = None,
                                       collection: Literal['rules', 'findings'] = 'rules',
                                       offset: OFFSET = 0, limit: LIMIT = 10) -> dict:
        """Читать закреплённые правила/нарушения без нового анализа; nextOffset продолжает страницу."""
        query = urlencode({'collection': collection, 'offset': offset, 'limit': limit})
        return await request('GET', route(projectId, '/' + quote(runId, safe='')) + '?' + query)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def read_architecture_evidence(runId: SMALL_ID, findingId: SMALL_ID, projectId: str | None = None,
                                         offset: OFFSET = 0, limit: LIMIT = 10) -> dict:
        """Читать направленный путь страницами. Фрагмент не означает полный цикл; смотрите total/nextOffset."""
        query = urlencode({'offset': offset, 'limit': limit})
        suffix = '/' + quote(runId, safe='') + '/findings/' + quote(findingId, safe='')
        return await request('GET', route(projectId, suffix) + '?' + query)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def read_architecture_source(runId: SMALL_ID, findingId: SMALL_ID, edgeIndex: OFFSET,
                                       projectId: str | None = None) -> dict:
        """Открыть ограниченный исходник доказательства. Смена snapshot/byte hash даёт409, чужой run404."""
        suffix = '/' + quote(runId, safe='') + '/findings/' + quote(findingId, safe='') + '/source'
        return await request('POST', route(projectId, suffix), {'edgeIndex': edgeIndex})
