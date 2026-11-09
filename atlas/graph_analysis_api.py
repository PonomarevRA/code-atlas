"""HTTP-слой анализа закреплённой карты и ограниченного чтения рабочей копии."""
from fastapi import FastAPI, Query

from .api_models import FederationInput, SourceInput, GraphNeighborsInput, ExecutionPathInput, CompareInput, MethodFlowChunkInput, AnalysisContextInput, AnalysisSectionInput
from .store import ProjectStore, StoreError
from .entrypoints import build_entrypoints
from .system_architecture import build_system_architecture
from .system_federation import build_federated_architecture
from .architecture_enrichment import enrich_architecture
from .node_description import build_node_description
from .execution_path import ExecutionPathError, build_execution_path
from .git_compare import GitCompareError, compare_graph
from .source_viewer import SourceError, read_node_source
from .graph_context import GraphContextError
from pydantic import BaseModel, Field, ConfigDict
from .source_workspace import indexed_files
from .mod_analysis import active_analysis_rules, analysis_settings_fingerprint
from .test_dataset_api import register_test_dataset_routes
from .html_api import register_html_routes
from .analysis_contexts import AnalysisContexts, AnalysisContextError


class SourceFilesInput(BaseModel):
    """Indexed names only; no arbitrary source path, filesystem walk or source execution."""
    snapshotId: str = Field(min_length=1, max_length=128)
    query: str = Field(default='', max_length=500)
    offset: int = Field(default=0, ge=0, le=100000, strict=True)
    limit: int = Field(default=100, ge=1, le=100, strict=True)
    model_config = ConfigDict(extra='forbid')


def register_graph_analysis_routes(app: FastAPI, store: ProjectStore, *,
                                   entrypoints_builder=build_entrypoints,
                                   federation_builder=build_federated_architecture,
                                   systems_builder=build_system_architecture,
                                   description_builder=build_node_description,
                                   description_reads_shared_graph: bool = False,
                                   path_builder=build_execution_path,
                                   comparison_builder=compare_graph,
                                   source_reader=read_node_source) -> None:
    """
    Подключает анализ без изменения правил snapshot и защищённого чтения файлов.

    Каждый маршрут закрепляет исходную ревизию существующим ProjectStore.
    Адаптеры передаются явно: модуль не импортирует composition root.

    Args:
        app: Приложение FastAPI, в котором регистрируются маршруты.
        store: Общее хранилище и владелец границ snapshot/maintenance.
        entrypoints_builder: Анализатор входов для закреплённого графа.
        federation_builder: Сборщик согласованной федерации проектов.
        systems_builder: Сборщик системной архитектуры.
        description_builder: Сборщик описания выбранного raw-узла.
        description_reads_shared_graph: Явное согласие проверенного адаптера на
            чтение общего графа без изменений. Произвольные адаптеры получают копию.
        path_builder: Сборщик статического пути выполнения.
        comparison_builder: Анализатор ограниченного сравнения Git.
        source_reader: Защищённый читатель исходного файла.
    Returns:
        None; маршруты добавляются к app без запуска анализа.
    """

    register_test_dataset_routes(app, store)
    register_html_routes(app, store)

    @app.get('/api/projects/{project_id}/overview')
    def project_overview(project_id: str, snapshotId: str | None = Query(default=None, max_length=128)):
        """
        Читает обзор без запуска восстановления или сканирования текущих исходников.

        Args:
            project_id: ID зарегистрированного проекта.
            snapshotId: Ожидаемая ревизия; несовпадение возвращает HTTP 409.
        Returns:
            Ограниченный обзор, в том числе для регистрации без построенной карты.
        Raises:
            StoreError: Неизвестный проект (404) или сменившийся снимок (409).
        """
        return store.project_overview(project_id, snapshotId)

    contexts = getattr(app.state, 'analysis_contexts', None)
    if contexts is None:
        contexts = app.state.analysis_contexts = AnalysisContexts()

    def context_settings(project_path):
        """Bind residency to the exact checkout and active mod settings, not a task label."""
        return str(project_path.resolve()) + ':' + analysis_settings_fingerprint(store.mods)

    @app.post('/api/projects/{project_id}/analysis-contexts')
    def open_analysis_context(project_id: str, body: AnalysisContextInput):
        """Open temporary ownership only after validating the current project snapshot."""
        root = store.source_scope(project_id, body.snapshotId)
        try:
            result = contexts.open(project_id, body.snapshotId, context_settings(root))
        except AnalysisContextError as error:
            raise StoreError(str(error), error.status) from error
        try:
            store.assert_snapshot(project_id, body.snapshotId)
        except StoreError:
            contexts.release(result['contextId'], project_id, body.snapshotId, context_settings(root))
            raise
        return result

    @app.post('/api/projects/{project_id}/analysis-contexts/{context_id}/close')
    def close_analysis_context(project_id: str, context_id: str, body: AnalysisContextInput):
        """Release a task's old snapshot scope even if a newer snapshot was published."""
        try:
            contexts.release(context_id, project_id, body.snapshotId)
        except AnalysisContextError as error:
            raise StoreError(str(error), error.status) from error
        return {'closed': True, 'contextId': context_id}

    @app.post('/api/projects/{project_id}/analysis-section')
    def analysis_section(project_id: str, body: AnalysisSectionInput):
        """Read full description fields through bounded JSON-tree pages, preserving analysis limits."""
        from .analysis_section_service import read_section
        try:
            result = read_section(store, contexts, project_id, body, description_builder, context_settings)
        except (AnalysisContextError, SourceError, ExecutionPathError) as error:
            raise StoreError(str(error), error.status) from error
        except ValueError as error:
            raise StoreError(str(error), 413) from error
        return result

    @app.get("/api/projects/{project_id}/entrypoints")
    def entrypoints(project_id: str, snapshotId: str = Query(min_length=1, max_length=128), capturedOnly: bool = False):
        """Вычисляет отдельный слой входов по рабочим файлам закреплённой карты.

        Результат не кэшируется: настройки рабочей копии могут измениться без
        переиндексации. Повторная проверка отклоняет публикацию новой карты
        во время сканирования, чтобы UI не связал входы с другой ревизией.
        """
        if capturedOnly:
            result = store.captured_entrypoint_snapshot(project_id, snapshotId)
            captured = result.get('analysisMods', {}).get('fingerprint')
        else:
            project_path, snapshot = store.comparison_snapshot(project_id)
            if snapshot["metadata"]["snapshotId"] != snapshotId:
                raise StoreError("Карта обновилась. Загрузите актуальный снимок перед просмотром входов.", 409)
            result = entrypoints_builder(project_path, snapshot, snapshotId)
            captured = snapshot.get('metadata', {}).get('analysisMods', {}).get('fingerprint')
        captured_settings = result.get('analysisMods', {}).get('settingsFingerprint') if capturedOnly else snapshot.get('metadata', {}).get('analysisMods', {}).get('settingsFingerprint')
        current = (captured_settings == analysis_settings_fingerprint(store.mods) if captured_settings
                   else captured == active_analysis_rules(store.mods)['fingerprint'])
        result['analysisModsCurrent'] = current
        if not current:
            result['warning'] = result.get('warning', '') + ' Правила модов изменились: обновите карту для нового обнаружения проектов и тестов.'
        store.assert_snapshot(project_id, snapshotId)
        return result

    @app.post("/api/projects/{project_id}/system-architecture")
    def federated_system_architecture(project_id: str, body: FederationInput):
        """
        Возвращает федерацию и source-bound ресурсы посещённых primary-объявлений.

        project_id выбирает зарегистрированный primary root; body закрепляет его snapshot,
        до 8 related snapshots и до 64 raw visits. Возвращает DTO архитектуры версии 2.
        Ошибки StoreError: 404 для неизвестных raw IDs, 409 для смены snapshot/исходника;
        структурные бюджеты остаются внутри существующих доверенных анализаторов.
        """
        snapshots = store.federation_snapshots(project_id, body.snapshotId, body.relatedProjectIds)
        result = federation_builder(snapshots, body.snapshotId)
        primary = next(item for item in snapshots if item['id'] == project_id)
        try:
            result = enrich_architecture(result, primary, body.visitedNodeIds)
        except ExecutionPathError as error:
            raise StoreError(str(error), error.status) from error
        store.assert_snapshots({item["id"]: item["snapshotId"] for item in snapshots})
        return result

    @app.get("/api/projects/{project_id}/system-architecture")
    def system_architecture(project_id: str, snapshotId: str = Query(min_length=1, max_length=128)):
        """Отдаёт отдельный слой внешних зависимостей без секретных настроек."""
        project_path, snapshot = store.comparison_snapshot(project_id)
        if snapshot["metadata"]["snapshotId"] != snapshotId:
            raise StoreError("Карта обновилась. Загрузите её перед обзором систем.", 409)
        result = systems_builder(project_path, snapshot, snapshotId)
        store.assert_snapshot(project_id, snapshotId)
        return result

    @app.post("/api/projects/{project_id}/node-description")
    def node_description(project_id: str, body: SourceInput):
        """
        Возвращает проверяемое описание одного узла и его текущего объявления.

        Args:
            project_id: Существующая регистрация проекта.
            body: Закреплённый snapshotId и raw-ID описываемого узла.
        Returns:
            Описание и completeness counters; диагностический узел не запускает semantic work.
        Raises:
            StoreError: Снимок сменился, узел неизвестен либо чтение недоступно.
        """
        def read_description(project_path, snapshot):
            """Build one owned response while treating all published facts as read-only."""
            result = description_builder(project_path, snapshot, body.nodeId, body.snapshotId)
            from .analysis_diagnostics import diagnostic_summary
            result.update(diagnostic_summary(snapshot))
            return result

        try:
            if description_reads_shared_graph:
                result = store._read_snapshot_analysis(project_id, body.snapshotId, read_description)
            else:
                project_path, snapshot = store.comparison_snapshot(project_id)
                if snapshot["metadata"]["snapshotId"] != body.snapshotId:
                    raise StoreError("Карта обновилась. Загрузите её перед описанием узла.", 409)
                result = read_description(project_path, snapshot)
        except ExecutionPathError as error:
            raise StoreError(str(error), error.status) from error
        store.assert_snapshot(project_id, body.snapshotId)
        if result.get('kind') == 'analysis-error':
            return result
        enrichment = getattr(app.state, 'semantic_enrichment', None)
        if enrichment is not None:
            result['semanticEngines'] = enrichment.use(project_id, body.snapshotId, [body.nodeId])
        result['sectionExploration'] = {'toolName': 'get_analysis_section', 'contextTool': 'open_analysis_context',
                                       'sections': ['methodFlow', 'documentation', 'calls', 'controlFlow', 'parameterFacts'],
                                       'contextRequired': True, 'analysisLimitsUnchanged': True}
        return result

    @app.post('/api/projects/{project_id}/method-flow-chunk')
    def method_flow_chunk(project_id: str, body: MethodFlowChunkInput):
        """Открывает одну страницу AST; ссылки не снимают ограничения одного запроса."""
        from .method_flow_chunks import build_method_flow_chunk
        project_path, snapshot = store.source_node_snapshot(project_id, body.nodeId, body.snapshotId)
        if snapshot['metadata']['snapshotId'] != body.snapshotId:
            raise StoreError('Карта обновилась. Загрузите её перед чтением AST.', 409)
        try:
            parse_cache = None
            settings = context_settings(project_path)
            if body.contextId is not None:
                from .analysis_section_service import cached_parser
                contexts.check(body.contextId, project_id, body.snapshotId, settings)
                parse_cache = cached_parser(contexts, body.contextId, project_id, body.snapshotId, settings)
            arguments = {'parse_cache': parse_cache} if parse_cache is not None else {}
            result = build_method_flow_chunk(project_path, snapshot, body.nodeId,
                                             body.snapshotId, body.chunkRef, body.limit, **arguments)
        except (SourceError, AnalysisContextError) as error:
            raise StoreError(str(error), error.status) from error
        store.assert_snapshot(project_id, body.snapshotId)
        if body.contextId is not None:
            try:
                if result.get('available'):
                    contexts.touch(body.contextId, project_id, body.snapshotId, settings)
            except AnalysisContextError as error:
                raise StoreError(str(error), error.status) from error
        return result

    def graph_context(project_id: str, body: SourceInput, neighbors: bool = False):
        """Читает факты одной закреплённой карты, независимо от рабочего файла."""
        try:
            if neighbors:
                continuation = {'cursor': body.cursor} if body.cursor is not None else {}
                result = store.graph_context_snapshot(project_id, body.nodeId, body.snapshotId,
                                                      body.direction, body.limit, **continuation)
            else:
                result = store.graph_context_snapshot(project_id, body.nodeId, body.snapshotId)
        except GraphContextError as error:
            raise StoreError(str(error), error.status) from error
        store.assert_snapshot(project_id, body.snapshotId)
        return result

    @app.post("/api/projects/{project_id}/graph-node")
    def graph_node(project_id: str, body: SourceInput):
        """Возвращает полный узел и захваченные атрибуты для агентского MCP."""
        return graph_context(project_id, body)

    @app.post("/api/projects/{project_id}/graph-neighbors")
    def graph_neighbors(project_id: str, body: GraphNeighborsInput):
        """Позволяет пройти дерево объявлений и атрибутов по фактам снимка."""
        return graph_context(project_id, body, neighbors=True)

    @app.post("/api/projects/{project_id}/execution-path")
    def execution_path(project_id: str, body: ExecutionPathInput):
        """Строит отдельный ограниченный статический путь для закреплённого снимка."""
        project_path, snapshot = store.comparison_snapshot(project_id)
        if snapshot["metadata"]["snapshotId"] != body.snapshotId:
            raise StoreError("Карта обновилась. Загрузите её перед анализом пути.", 409)
        try:
            result = path_builder(project_path, snapshot, body.nodeId, body.snapshotId,
                                          body.maxDepth, body.maxNodes)
        except ExecutionPathError as error:
            raise StoreError(str(error), error.status) from error
        store.assert_snapshot(project_id, body.snapshotId)
        return result

    @app.post("/api/projects/{project_id}/compare")
    def compare(project_id: str, body: CompareInput):
        """Подсвечивает узлы изменённых файлов относительно локальной Git-ревизии."""
        project_path, graph_snapshot = store.comparison_snapshot(project_id)
        try:
            result = comparison_builder(project_path, graph_snapshot, body.baseRef, body.targetRef, body.mode)
        except GitCompareError as error:
            raise StoreError(str(error), error.status) from error
        result["snapshotId"] = graph_snapshot["metadata"]["snapshotId"]
        # Git commands may overlap publication; highlights belong only to the captured map.
        store.assert_snapshot(project_id, result["snapshotId"])
        return result

    @app.post("/api/projects/{project_id}/source")
    def source(project_id: str, body: SourceInput):
        """Отдаёт ограниченный текущий исходник узла одной подтверждённой ревизии."""
        project_path, node = store.source_snapshot(project_id, body.nodeId, body.snapshotId)
        try:
            return source_reader(project_path, node, body.snapshotId)
        except SourceError as error:
            raise StoreError(str(error), error.status) from error

    @app.post('/api/projects/{project_id}/source-files')
    def source_files(project_id: str, body: SourceFilesInput):
        """List safe paths already captured in one current map, never creating a file or a cache."""
        _, graph = store.comparison_snapshot(project_id)
        if graph['metadata']['snapshotId'] != body.snapshotId:
            raise StoreError('Карта обновилась. Загрузите её перед просмотром файлов.', 409)
        result = indexed_files(graph, body.snapshotId, body.query, body.offset, body.limit)
        store.assert_snapshot(project_id, body.snapshotId)
        return result
