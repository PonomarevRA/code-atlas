"""Stdio MCP для агентских видов через работающий Atlas; собственного ProjectStore нет."""

import argparse
import json
import logging
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .test_coverage_mcp import project_test_coverage, validate_coverage_query
from .node_comments import COMMENT_FIELDS
from .data_mcp import register_data_tools
from .reference_mcp import register_reference_tools
from .pattern_catalog_mcp import register_pattern_tools
from .module_analysis_mcp import register_module_analysis_tools
from .debug_feedback_mcp import register_debug_feedback_tools
from .checkout_maps_mcp import register_checkout_tools
from .change_review_mcp import register_change_review_tools
from .position_context_mcp import register_position_context_tools
from .module_proposal_mcp import register_module_proposal_tools
from .library_mcp import register_library_tools
from .code_health_mcp import register_health_tools
from .mcp_response_budget import bound_list_response, compact_description
from .mcp_transport import LoopbackTransport
from .code_health_record_mcp import register_health_record_tool
from .test_dataset_mcp import register_test_data_tools
from .html_mcp import register_html_tools
from .architecture_mcp import register_architecture_tools
from .browser_mcp import register_browser_tools
from .document_mcp import register_document_tools
from .document_search_mcp import register_document_search_tools
from .semantic_engine_mcp import register_semantic_engine_tools


def create_mcp(port: int = 8765, project_id: str | None = None, data_map_id: str | None = None) -> FastMCP:
    """
    Создаёт инструменты строго loopback HTTP-прокси без чтения файлов индекса.

    Args:
        port: Loopback-порт Atlas от 1024 до 65535.
        project_id: Необязательная привязка инструментов к одной регистрации.
        data_map_id: Необязательная привязка инструментов к одной карте данных.
    Returns:
        Сервер с HTTP-прокси инструментами без собственного хранилища.
    Raises:
        ValueError: Порт или привязанные идентификаторы некорректны.
    """
    if not 1024 <= port <= 65535:
        raise ValueError("Порт должен быть от 1024 до 65535")
    if project_id is not None and (not project_id.strip() or len(project_id) > 128 or any(ord(char) < 32 for char in project_id)):
        raise ValueError("Некорректный ID выбранного проекта")
    if data_map_id is not None and (not data_map_id.strip() or len(data_map_id) > 128 or any(ord(char) < 32 for char in data_map_id)):
        raise ValueError("Некорректный ID карты данных")
    base = f"http://127.0.0.1:{port}"
    transport = LoopbackTransport(port)
    server = FastMCP("Code Atlas · сохранённые виды", instructions=(
        "При анализе кода проверяйте readabilityRecommendations в describe_node. "
        "Это рекомендации для улучшения дерева, а не ошибки. При разрешённом изменении кода "
        "рассмотрите выделение сложного return в result с последующим return result;, сохраняя семантику. "
        "При запросе только схемы помечайте рекомендацию заметкой, не изменяя исходники. "
        "Полные правила доступны в skill Code Atlas (/api/agent-setup/skill). "
        "Before authorized code changes, capture working-tree health, plan_code_changes, then measure/compare after editing. "
        "Health forecasts are estimates and documentation presence is not semantic accuracy or behavioral proof."
    ), lifespan=transport.lifespan)

    def selected_project(requested: str | None) -> str:
        """Подставляет привязанный проект и отклоняет чужой до обращения к HTTP API."""
        selected = requested if requested is not None else project_id
        if not selected:
            raise ValueError("Укажите projectId из list_projects.")
        if project_id is not None and selected != project_id:
            raise ValueError("Этот MCP подключён к другому проекту. Используйте выбранный проект.")
        return selected

    request = transport.request
    register_architecture_tools(server, request, selected_project)
    register_semantic_engine_tools(server, request, selected_project, project_id)

    def summary(view: dict) -> dict:
        """Возвращает ссылки и компактные факты, не заполняя MCP-контекст полным raw-графом."""
        result = {key: view[key] for key in ("id", "title", "projectId", "snapshotId", "createdAt")}
        result["metadata"] = {key: view["metadata"].get(key) for key in ("sourceSnapshotId", "truncated", "kindFilterWarning", "schemaVersion")}
        result.update({"url": base + view["url"], "pngUrl": base + view["pngUrl"],
                       "nodeCount": len(view["nodes"]), "edgeCount": len(view["edges"]),
                       "annotationCount": len(view["annotations"])})
        return result

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_projects() -> dict:
        """
        Список зарегистрированных репозиториев; search_nodes возвращает актуальный snapshotId.

        Returns:
            До 100 регистраций; analysisStatus=unknown не подтверждает полноту старой карты.
        """
        result = await request("GET", "/api/projects")
        fields = ('id', 'name', 'status', 'nodeCount', 'edgeCount', 'snapshotId',
                  'analysisStatus', 'errorCount', 'programNodeCount', 'diagnosticNodeCount',
                  'failedFileCount', 'excludedDataCount')
        projects = []
        for project in result['projects']:
            if project_id is None or project.get('id') == project_id:
                projects.append({key: project.get(key) for key in fields})
        return {"projects": projects[:100], "truncated": len(projects) > 100}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def search_nodes(query: str, projectId: str | None = None, snapshotId: str | None = None,
                           limit: int = 120, excludeKinds: list[str] | None = None) -> dict:
        """
        Найти raw-ID по имени/пути; comment и implementationComment показывают захваченные пояснения, если они есть.

        Исходники не загружаются; commentScope различает объявление и окружающий тип.
        commentSource содержит provenance; snapshotId нужен для продолжения чтения.
        complexity exposes captured local nesting/branching scores, version and analysis status.
        Ищет также захваченные названия тестов, метки, аннотации и ссылки на провайдеры данных.
        analysisFacts.entrypoints показывает HTTP-маршруты и MCP-операции из включённых библиотечных модов.
        analysisFacts содержит краткие сведения языковых/библиотечных модов; get_node даёт детали.
        analysisContext фиксирует провайдеры снимка. Это не выполнение тестов и не перечень текущих модов.

        Args:
            query: Подстрока имени, пути или raw-ID.
            projectId: Явная регистрация либо привязка текущего MCP.
            snapshotId: Ожидаемая ревизия либо текущая карта.
            limit: Бюджет результатов, проверяемый HTTP API.
            excludeKinds: Необязательные исключаемые типы узлов.
        Returns:
            Ограниченная страница с независимыми от обрезки счётчиками неполноты.
        """
        projectId = selected_project(projectId)
        body = {"query": query, "limit": limit, "excludeKinds": excludeKinds or []}
        if snapshotId:
            body["snapshotId"] = snapshotId
        result = await request("POST", f"/api/projects/{quote(projectId, safe='')}/search-nodes", body)
        bounded = bound_list_response(result, ("nodes",))
        if bounded is None:
            return {"snapshotId": result.get("snapshotId"), "total": result.get("total"), "nodes": [],
                    **{key: result[key] for key in ('analysisStatus', 'errorCount', 'coverage', 'failedFileCount', 'excludedDataCount', 'processedFileCount', 'sourceFileCount') if key in result},
                    "truncated": True, "warning": "Narrow the query; response metadata exceeds the MCP budget."}
        return bounded

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_node(snapshotId: str, nodeId: str, projectId: str | None = None) -> dict:
        """Прочитать захваченные факты узла и attributeGraph из выбранного снимка.

        memberFacts показывает язык, private/public и объявление владельца; fieldFacts,
        dataFacts/staticData содержат поля, константы и исходные пары словарей.
        complexity contains local AST nesting, branching, loops and a versioned structural score.
        Check status and sourceHash before comparisons; this is not execution cost or callee depth.
        Исходники повторно не читаются. Большие attributeFacts сокращаются с truncated,
        attributeNodeIds позволяют продолжить чтение атрибутов через get_neighbors.
        comment и implementationComment сразу содержат пояснения из снимка, если они есть.
        """
        projectId = selected_project(projectId)
        return await request("POST", f"/api/projects/{quote(projectId, safe='')}/graph-node",
                             {"snapshotId": snapshotId, "nodeId": nodeId})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_neighbors(snapshotId: str, nodeId: str, projectId: str | None = None,
                            direction: str = "both", limit: int = 40, cursor: str | None = None) -> dict:
        """Прочитать входящие/исходящие связи снимка и компактные соседние узлы.

        direction: incoming/outgoing/both; limit: 1..120 рёбер. Это одно соседство,
        без исполнения кода, нового анализа или трактовки атрибутов как вызовов.
        reads/writes — обращения к данным; method/field — принадлежность, а не вызов.
        Компактные узлы включают memberFacts, первые пары dataFacts и comment и назначение реализации, если они захвачены.
        nextRef продолжает чтение через cursor; другой snapshot/node/direction отклоняется.
        """
        projectId = selected_project(projectId)
        body = {"snapshotId": snapshotId, "nodeId": nodeId, "direction": direction, "limit": limit}
        if cursor is not None:
            body['cursor'] = cursor
        return await request("POST", f"/api/projects/{quote(projectId, safe='')}/graph-neighbors", body)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def open_analysis_context(snapshotId: str, projectId: str | None = None) -> dict:
        """Open a RAM-only task scope; useful reads renew 20-minute idle expiry, close releases it."""
        selected = selected_project(projectId)
        return await request('POST', f'/api/projects/{quote(selected, safe="")}/analysis-contexts',
                             {'snapshotId': snapshotId})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def close_analysis_context(snapshotId: str, contextId: str, projectId: str | None = None) -> dict:
        """Release this explicit analysis scope, preserving all published snapshots and other tasks."""
        selected = selected_project(projectId)
        return await request('POST', f'/api/projects/{quote(selected, safe="")}/analysis-contexts/{quote(contextId, safe="")}/close',
                             {'snapshotId': snapshotId})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_analysis_section(
        snapshotId: str, nodeId: str, contextId: str, projectId: str | None = None,
        section: Literal['methodFlow', 'documentation', 'calls', 'controlFlow', 'parameterFacts'] = 'methodFlow',
        sectionRef: str | None = None, limit: int = 40,
    ) -> dict:
        """Read description details omitted by MCP budgets as bounded JSON-tree pages.

        Open a context first. items[].ref expands that child; nextRef continues
        this page through sectionRef. Coverage retains analyzer truncation and
        UNKNOWN facts; pagination does not compute unsupported semantic flow.
        """
        selected = selected_project(projectId)
        body = {'snapshotId': snapshotId, 'nodeId': nodeId, 'contextId': contextId,
                'section': section, 'limit': limit}
        if sectionRef is not None:
            body['sectionRef'] = sectionRef
        return await request('POST', f'/api/projects/{quote(selected, safe="")}/analysis-section', body)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def describe_node(snapshotId: str, nodeId: str, projectId: str | None = None) -> dict:
        """Получить факты, документацию, ветви и readabilityRecommendations для сложных return.

        Рекомендации не являются ошибками. В разрешённой задаче изменения кода рассмотрите
        result = выражение; return result; с корректным типом и сохранением поведения.
        При построении схемы без изменения кода добавьте заметку с файлом и строкой.
        complexity contains versioned local AST metrics: score, maximum/average nesting,
        branching and loops. Check status/provenance; partial scores are lower bounds.
        Callees and deferred bodies are excluded; the score is not runtime cost.
        testFacts, codeProjectFacts и analysisFacts дополняют описание захваченными данными модов.
        Их snapshot sourceKind отделён от текущего sourceProvenance тела метода; runtimeStatus остаётся unknown.
        """
        projectId = selected_project(projectId)
        result = await request("POST", f"/api/projects/{quote(projectId, safe='')}/node-description",
                               {"snapshotId": snapshotId, "nodeId": nodeId})
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 64_000:
            return compact_description(result, snapshotId, nodeId, COMMENT_FIELDS)
        return result

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_semantic_engine_status(
        snapshotId: Annotated[str, Field(min_length=1, max_length=128)],
        projectId: str | None = None,
    ) -> dict:
        """Прочитать доступность локальных движков и статус уточнения конкретной карты.

        При активном использовании приложение само проверяет среду обычно раз
        в сутки. Агент не обязателен; эти же состояния доступны в интерфейсе.
        available означает совместимый движок, а не полноту анализа или PASS.
        """
        selected = selected_project(projectId)
        return await request('GET', f'/api/projects/{quote(selected, safe="")}/semantic-enrichment?snapshotId={quote(snapshotId, safe="")}')

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_method_flow_chunk(
        snapshotId: Annotated[str, Field(min_length=1, max_length=128)],
        nodeId: Annotated[str, Field(min_length=1, max_length=4096)],
        projectId: str | None = None,
        chunkRef: Annotated[str, Field(min_length=1, max_length=8192)] | None = None,
        limit: Annotated[int, Field(strict=True, ge=1, le=64)] = 40,
        contextId: str | None = None,
    ) -> dict:
        """Узнать, есть ли глубже синтаксис выбранного узла, и открыть его по ссылке.

        Первый запрос без chunkRef открывает объявление. children[].ref открывает
        поддерево; nextRef продолжает страницу. Ссылки закреплены за nodeId,
        snapshotId и байтами исходника: после изменения файла начните заново.
        Это syntax-outline, не полный method-flow и не доказательство исполнения.
        Лимиты method-flow и статус недоступных рекомендаций остаются прежними.
        Для серии чтений откройте open_analysis_context и передавайте contextId:
        неизменившийся файл переиспользует разбор до20 минут неактивности.
        """
        selected = selected_project(projectId)
        body = {'snapshotId': snapshotId, 'nodeId': nodeId, 'limit': limit}
        if chunkRef is not None:
            body['chunkRef'] = chunkRef
        if contextId is not None:
            body['contextId'] = contextId
        return await request('POST', f'/api/projects/{quote(selected, safe="")}/method-flow-chunk', body)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_test_coverage(
        snapshotId: Annotated[str, Field(min_length=1, max_length=128)],
        projectId: str | None = None,
        nodeId: Annotated[str, Field(min_length=1, max_length=1024)] | None = None,
        section: Literal["summary", "conditions", "tests"] = "summary",
        query: Annotated[str, Field(max_length=500)] = "",
        kind: Literal["all", "if", "switch", "loop"] = "all",
        runtimeStatus: Literal["all", "unknown", "partial", "uncovered", "covered"] = "all",
        testKind: Literal["all", "unit", "gherkin"] = "all",
        offset: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=40)] = 20,
        conditionId: str | None = None,
        testId: str | None = None,
        analysisId: str | None = None,
        reportPaths: list[str] | None = None,
    ) -> dict:
        """Прочитать сводку покрытия, страницу условий или unit/Gherkin-тестов.

        nodeId ограничивает условия цепочкой вызовов raw метода; тесты — инвентарь
        просканированных файлов репозитория. section=conditions/tests возвращает
        страницы, conditionId/testId — детали. Для продолжения передавайте
        analysisId из первого ответа и pagination.nextOffset: изменение текущих
        исходников отклонит продолжение. Без Cobertura выполнение неизвестно;
        статические связи и цели true/false не доказывают покрытие.
        Отчёты reportPaths читаются внутри репозитория; тесты не запускаются.
        Каждый запрос повторяет ограниченный анализ; ответ компактнее страницы UI.
        """
        selected = selected_project(projectId)
        filters = validate_coverage_query(section, query, kind, runtimeStatus, testKind,
                                          offset, limit, conditionId, testId, analysisId, reportPaths)
        body = {"snapshotId": snapshotId, "reportPaths": filters["reportPaths"]}
        if nodeId is not None:
            body["nodeId"] = nodeId
        result = await request("POST", f"/api/projects/{quote(selected, safe='')}/test-coverage", body, timeout=120)
        if result.get("projectId") != selected or result.get("snapshotId") != snapshotId or (result.get("scope") or {}).get("nodeId") != nodeId:
            raise ValueError("Ответ покрытия относится к другому проекту, снимку или цепочке. Повторите запрос.")
        return project_test_coverage(result, filters, base)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def search_documentation(snapshotId: str, query: str, projectId: str | None = None,
                                   limit: int = 8, kind: str = "all") -> dict:
        """Найти выдержки комментариев/документов в локальном LanceDB и связанные raw-ID.

        Индекс должен быть подготовлен index_documentation. Результат содержит
        расстояние, точные источники и подтверждённые связи; это не ответ LLM.
        """
        projectId = selected_project(projectId)
        result = await request("POST", f"/api/projects/{quote(projectId, safe='')}/semantic-search",
                               {"snapshotId": snapshotId, "query": query, "limit": limit, "kind": kind}, timeout=60)
        bounded = bound_list_response(result, ("hits",))
        if bounded is None:
            return {"snapshotId": snapshotId, "truncated": True, "hits": [],
                    "warning": "Ответ превышает MCP-бюджет. Уменьшите limit."}
        return bounded

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def index_documentation(snapshotId: str, projectId: str | None = None, force: bool = False) -> dict:
        """Снять комментарии/документы для карты и поставить локальную индексацию в фон.

        Модель должна быть подготовлена заранее. Исходный репозиторий не изменяется.
        Возвращает job.id для get_documentation_index; данные не отправляются в сеть.
        """
        projectId = selected_project(projectId)
        return await request("POST", f"/api/projects/{quote(projectId, safe='')}/documentation-index",
                             {"snapshotId": snapshotId, "force": force})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_documentation_index(snapshotId: str, projectId: str | None = None) -> dict:
        """Проверить готовность производного индекса и активный job без загрузки модели."""
        projectId = selected_project(projectId)
        return await request("GET", f"/api/projects/{quote(projectId, safe='')}/documentation-index?snapshotId={quote(snapshotId, safe='')}")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def create_view(snapshotId: str, title: str, projectId: str | None = None, nodeIds: list[str] | None = None,
                          search: str = "", query: str = "", excludeKinds: list[str] | None = None,
                          maxNodes: int = 120, depth: int = 1, annotations: list[dict] | None = None,
                          excludeNodeIds: list[str] | None = None) -> dict:
        """Создать неизменяемый вид: выбирайте raw-ID; заметки {id,title,text,nodeIds} авторские.

        Сначала найдите символы. Размеченные поля убирает excludeKinds=['field'];
        для неразмеченных symbol используйте describe_node и точные excludeNodeIds. Соседство
        depth идёт в обе стороны и не является порядком исполнения. До 120 узлов,
        30 заметок с текстом до 1200 символов. Все ссылки заметок должны попасть
        в выборку. Полученные URL открывают сохранённый вид и его PNG.
        """
        projectId = selected_project(projectId)
        view = await request("POST", "/api/views", {"projectId": projectId, "snapshotId": snapshotId,
            "title": title, "nodeIds": nodeIds or [], "search": search, "query": query,
            "excludeKinds": excludeKinds or [], "excludeNodeIds": excludeNodeIds or [], "maxNodes": maxNodes, "depth": depth, "annotations": annotations or []})
        return summary(view)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_view(viewId: str) -> dict:
        """Прочитать вид, захваченные комментарии узлов и авторские заметки; полная модель доступна по HTTP API."""
        view = await request("GET", f"/api/views/{quote(viewId, safe='')}")
        if project_id is not None and view.get("projectId") != project_id:
            raise ValueError("Сохранённый вид относится к другому проекту.")
        result = {**summary(view), "nodes": [{key: node.get(key) for key in ("id", "label", "kind", "codeProjectId", "complexity", "health", "documentationHealth", "healthIdentity", *COMMENT_FIELDS) if key in node} for node in view["nodes"]],
                  "annotations": view["annotations"]}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 64_000:
            result = {**summary(view), "truncated": True, "apiUrl": base + f"/api/views/{view['id']}"}
        return result
    register_data_tools(server, request, selected_project, project_id, data_map_id)
    register_reference_tools(server, request)
    register_pattern_tools(server, request)
    register_module_analysis_tools(server, request, selected_project)
    register_debug_feedback_tools(server, request, selected_project)
    register_checkout_tools(server, request, project_id, data_map_id)
    register_change_review_tools(server, request, base, project_id, data_map_id)
    register_position_context_tools(server, request, selected_project, project_id, data_map_id)
    register_module_proposal_tools(server, request, selected_project)
    register_library_tools(server, request, selected_project)
    register_health_tools(server, request, selected_project)
    register_test_data_tools(server, request, selected_project)
    register_html_tools(server, request, selected_project)
    register_browser_tools(server, request, project_id, data_map_id)
    if data_map_id is None:
        from .desktop_automation_mcp import register_desktop_tools
        register_desktop_tools(server, request, selected_project, project_id)
    register_document_tools(server, request, selected_project, project_id, data_map_id)
    register_document_search_tools(server, request, selected_project, project_id, data_map_id)
    register_health_record_tool(server, request, selected_project, base)
    return server


def main() -> None:
    """Запускает только stdio-прокси; HTTP сервер и индексация остаются в Atlas."""
    parser = argparse.ArgumentParser(description="Code Atlas agent MCP")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--project-id", help="Привязать инструменты к одному зарегистрированному проекту")
    parser.add_argument("--data-map-id", help="Привязать инструменты данных к одной самостоятельной карте")
    args = parser.parse_args()
    import anyio
    from .mcp_stdio import run_stdio
    # Cursor treats stderr as an error stream, including routine SDK and HTTP logs.
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server = create_mcp(args.port, args.project_id, args.data_map_id)
    anyio.run(run_stdio, server)


if __name__ == "__main__":
    main()
