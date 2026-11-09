"""MCP нового слоя: явное построение, область карты и дата/свежесть каждого ответа."""

from urllib.parse import quote

from mcp.types import ToolAnnotations

from .data_models import MAX_RESPONSE_BYTES
from .data_storage import encode

DATA_TOOLS = ["discover_data_files", "list_data_maps", "create_data_map", "ensure_data_map", "get_data_job",
              "cancel_data_job", "get_data_overview", "get_data_schema", "find_data_relations", "query_data_records",
              "get_data_record", "search_data_scenarios", "get_data_scenario", "trace_data_links", "create_data_view",
              "rank_data_keys", "get_data_key_values", "get_data_key_path"]


def register_data_tools(server, request, selected_project, project_id=None, map_id=None):
    """Добавляет инструменты, не создавая локальное хранилище в stdio-процессе."""
    read = ToolAnnotations(readOnlyHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

    def bounded(result):
        """Структура и списки также имеют байтовый бюджет; усечение всегда явно."""
        if len(encode(result).encode()) <= MAX_RESPONSE_BYTES:
            return result
        for field in ("maps", "files", "items", "nodes"):
            values = result.get(field)
            if isinstance(values, list):
                while values and len(encode(result).encode()) > MAX_RESPONSE_BYTES:
                    values.pop()
                    result["truncated"] = True
        if len(encode(result).encode()) > MAX_RESPONSE_BYTES:
            return {"mapId": result.get("mapId", result.get("id")), "snapshotId": result.get("snapshotId"),
                    "builtAt": result.get("builtAt"), "lastVerifiedAt": result.get("lastVerifiedAt"), "freshness": result.get("freshness"),
                    "truncated": True, "warning": "Сузьте запрос или прочитайте выбранную коллекцию/поля."}
        return result

    async def selected_map(requested):
        """Фиксированная карта проверяется до HTTP; принадлежность проекту — до выдачи."""
        selected = requested or map_id
        if not selected or len(selected) > 128:
            raise ValueError("Укажите mapId из list_data_maps или create_data_map.")
        if map_id and selected != map_id:
            raise ValueError("Этот MCP привязан к другой карте данных.")
        result = await request("GET", "/api/data-maps/" + quote(selected, safe=""))
        if project_id and result.get("projectId") != project_id:
            raise ValueError("Карта данных не относится к выбранному проекту.")
        return selected, result

    async def query(selected, snapshot, operation, **values):
        """API сообщает свежесть; решение об обновлении принадлежит агенту/пользователю."""
        selected, _ = await selected_map(selected)
        result = await request("POST", f"/api/data-maps/{quote(selected, safe='')}/query/{operation}", {"snapshotId": snapshot, **values})
        if result.get("mapId") != selected or result.get("snapshotId") != snapshot:
            raise ValueError("Ответ относится к другой карте или снимку.")
        return bounded(result)

    @server.tool(annotations=read)
    async def discover_data_files(projectId: str | None = None, root: str | None = None) -> dict:
        """Найти CSV/JSON/JSONL/.feature по именам. Содержимое не читается, карта не строится."""
        if map_id:
            raise ValueError("Подключение фиксированной карты не обнаруживает другие источники.")
        if project_id or projectId:
            selected = selected_project(projectId)
            if root is not None:
                raise ValueError("Для проекта root не передаётся.")
            return bounded(await request("GET", f"/api/projects/{quote(selected, safe='')}/data-files"))
        if not root:
            raise ValueError("Укажите root или projectId.")
        return bounded(await request("POST", "/api/data-files", {"root": root}))

    @server.tool(annotations=read)
    async def list_data_maps(projectId: str | None = None) -> dict:
        """Список уже созданных карт и их актуальность; не сканирует и не строит файлы."""
        if map_id:
            _, value = await selected_map(map_id)
            return bounded({"maps": [value]})
        suffix = ""
        if project_id or projectId:
            suffix = "?projectId=" + quote(selected_project(projectId), safe="")
        return bounded(await request("GET", "/api/data-maps" + suffix))

    @server.tool(annotations=write)
    async def create_data_map(files: list[str], title: str = "Карта данных", projectId: str | None = None,
                              root: str | None = None, encoding: str = "utf-8-sig", delimiter: str = "auto",
                              indexFields: list[str] | None = None, relations: list[dict] | None = None) -> dict:
        """Явно построить выбранные файлы до 1 ГБ. Возвращает job: дождитесь completed.

        Запрос анализа файлов разрешает построение карты для этой задачи; открытие
        проекта и discover_data_files не разрешают построение автоматически.
        Значения ключей сравниваются typed; compareAs=string в явном правиле
        допускает CSV-строку и JSON-число без удаления ведущих нулей.
        """
        if map_id:
            raise ValueError("Для фиксированной карты используйте ensure_data_map.")
        body = {"files": files, "title": title, "encoding": encoding, "delimiter": delimiter,
                "indexFields": indexFields or [], "relations": relations or []}
        if project_id or projectId:
            body["projectId"] = selected_project(projectId)
            if root is not None:
                raise ValueError("Для проекта root не передаётся.")
        else:
            body["root"] = root
        return bounded(await request("POST", "/api/data-maps", body, timeout=120))

    @server.tool(annotations=write)
    async def ensure_data_map(mapId: str | None = None, force: bool = False) -> dict:
        """Перед анализом явно проверить SHA-256 и обновить только изменённую карту.

        Если есть job, дождитесь completed через get_data_job, затем получите новый
        snapshotId. Чтения возвращают builtAt/freshness и сами карту не перестраивают.
        force=True нужен лишь для явной полной пересборки без изменения файлов.
        """
        selected, _ = await selected_map(mapId)
        return bounded(await request("POST", f"/api/data-maps/{quote(selected, safe='')}/refresh", {"force": force}, timeout=120))

    @server.tool(annotations=read)
    async def get_data_job(jobId: str) -> dict:
        """Проверить завершение/ошибку явно запрошенного импорта, не запускать новый."""
        value = await request("GET", "/api/data-jobs/" + quote(jobId, safe=""))
        await selected_map(value.get("mapId"))
        return bounded(value)

    @server.tool(annotations=write)
    async def cancel_data_job(jobId: str) -> dict:
        """Отменить импорт выбранной карты, сохранив последний опубликованный снимок."""
        value = await request("GET", "/api/data-jobs/" + quote(jobId, safe=""))
        await selected_map(value.get("mapId"))
        return bounded(await request("POST", "/api/data-jobs/" + quote(jobId, safe="") + "/cancel", {}))

    @server.tool(annotations=read)
    async def get_data_overview(mapId: str | None = None) -> dict:
        """Получить дату/свежесть снимка. Агент решает, нужен ли ensure_data_map."""
        _, result = await selected_map(mapId)
        return bounded(result)

    @server.tool(annotations=read)
    async def get_data_schema(snapshotId: str, mapId: str | None = None, nodeId: str | None = None,
                              cursor: str | None = None, limit: int = 30) -> dict:
        """Дерево файлов, коллекций, полей и сценариев. nodeId ограничивает дочерние узлы."""
        return await query(mapId, snapshotId, "schema", nodeId=nodeId, cursor=cursor, limit=limit)

    @server.tool(annotations=read)
    async def find_data_relations(snapshotId: str, mapId: str | None = None, cursor: str | None = None, limit: int = 30) -> dict:
        """Кандидаты и заданные правила с полным охватом/нарушениями. Совпадение не доказывает бизнес-смысл."""
        return await query(mapId, snapshotId, "relations", cursor=cursor, limit=limit)

    @server.tool(annotations=read)
    async def rank_data_keys(snapshotId: str, mapId: str | None = None, collectionId: str | None = None,
                             cursor: str | None = None, limit: int = 30) -> dict:
        """Ключи по повторениям и межфайловым совпадениям. null не повышает рейтинг; карта не строится."""
        return await query(mapId, snapshotId, "keys", collectionId=collectionId, cursor=cursor, limit=limit)

    @server.tool(annotations=read)
    async def get_data_key_values(snapshotId: str, keyId: str, mapId: str | None = None,
                                  cursor: str | None = None, limit: int = 20) -> dict:
        """Значения выбранного ключа по частоте; valueRecordId выбирает группу без передачи больших значений."""
        return await query(mapId, snapshotId, "key-values", keyId=keyId, cursor=cursor, limit=limit)

    @server.tool(annotations=read)
    async def get_data_key_path(snapshotId: str, keyId: str, valueRecordId: str, mapId: str | None = None,
                                cursor: str | None = None, limit: int = 20, maxDepth: int = 2,
                                maxNodes: int = 60, relationIds: list[str] | None = None) -> dict:
        """Изолированный путь ключ → значение → строки → связи, с датой снимка и бюджетом графа.

        nextCursor продолжает строки группы; дальнейший fan-out отдельной строки
        доступен в trace_data_links. Совпадения не подтверждают бизнес-смысл.
        """
        return await query(mapId, snapshotId, "key-path", keyId=keyId, valueRecordId=valueRecordId,
                           cursor=cursor, limit=limit, maxDepth=maxDepth, maxNodes=maxNodes, relationIds=relationIds or [])

    @server.tool(annotations=read)
    async def query_data_records(snapshotId: str, collectionId: str, mapId: str | None = None,
                                 filters: list[dict] | None = None, fields: list[str] | None = None,
                                 cursor: str | None = None, limit: int = 20) -> dict:
        """Страница записей. filters: {field:'/id',op:'eq'|'ne'|'isNull'|'missing',value,type}.

        decimal передавайте строкой с type='number'. Числа сохраняют точность;
        CSV ведущие нули остаются строками. fields — пути JSON Pointer. Нет raw SQL.
        """
        return await query(mapId, snapshotId, "records", collectionId=collectionId, filters=filters or [], fields=fields or [], cursor=cursor, limit=limit)

    @server.tool(annotations=read)
    async def get_data_record(snapshotId: str, recordId: str, mapId: str | None = None, fields: list[str] | None = None) -> dict:
        """Запись и источник захваченного снимка. При усечении запросите конкретные fields."""
        return await query(mapId, snapshotId, "record", recordId=recordId, fields=fields or [])

    @server.tool(annotations=read)
    async def search_data_scenarios(snapshotId: str, mapId: str | None = None, queryText: str = "", tag: str = "",
                                   cursor: str | None = None, limit: int = 20) -> dict:
        """Найти Gherkin-сценарии и сразу увидеть их пояснения, теги и строки источника."""
        return await query(mapId, snapshotId, "scenarios", query=queryText, tag=tag, cursor=cursor, limit=limit)

    @server.tool(annotations=read)
    async def get_data_scenario(snapshotId: str, scenarioId: str, mapId: str | None = None, exampleId: str | None = None,
                                cursor: str | None = None, limit: int = 20) -> dict:
        """Gherkin: фон, шаги, ожидания и выбранные Examples. runtimeStatus остаётся unknown."""
        return await query(mapId, snapshotId, "scenario", scenarioId=scenarioId, exampleId=exampleId, cursor=cursor, limit=limit)

    @server.tool(annotations=read)
    async def trace_data_links(snapshotId: str, recordId: str, mapId: str | None = None, relationIds: list[str] | None = None,
                               maxDepth: int = 2, maxNodes: int = 60, cursor: str | None = None) -> dict:
        """Локальный путь до 120 записей; nextCursor/continuations продолжают fan-out."""
        return await query(mapId, snapshotId, "trace", recordId=recordId, relationIds=relationIds or [], maxDepth=maxDepth, maxNodes=maxNodes, cursor=cursor)

    @server.tool(annotations=write)
    async def create_data_view(snapshotId: str, recordId: str, title: str, mapId: str | None = None,
                               relationIds: list[str] | None = None, maxDepth: int = 2, maxNodes: int = 60) -> dict:
        """Сохранить ограниченный подграф отдельного слоя; вид исторический, не live-доказательство."""
        return await query(mapId, snapshotId, "view", recordId=recordId, title=title, relationIds=relationIds or [], maxDepth=maxDepth, maxNodes=maxNodes)
