"""HTTP API и статический интерфейс, доступные только на loopback."""

import asyncio
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .local_http_boundary import LocalHTTPBoundary
from .http_lifecycle import HTTPLifecycle, HTTPState
from .agent_activity.service import ActivityJournal
from .agent_activity.observer import ActivityObserver
from .agent_activity.api import register_activity_routes
from .technical_logs.service import TechnicalJournal
from .technical_logs.http_api import register_technical_routes
from .technical_logs.metrics import register_diagnostics_routes
from .map_payload import serialize_graph
from .map_exports import register_map_export_routes
from .progressive_map_api import register_progressive_map_routes
from .api_models import ProjectInput, RemoteRepositoryInput, NodeInput, QueryInput, AgentViewInput, AgentSearchInput

from .mcp_bridge import describe_node, query_graph
from .git_compare import compare_graph
from .grouping_providers import DEFAULT_ALGORITHM, list_grouping_algorithms
from .store import ProjectStore, StoreError
from .remote_repositories import RemoteRepositories
from .runtime_paths import managed_repositories_dir, default_data_dir, resource_root
from .source_viewer import read_node_source
from .entrypoints import build_entrypoints
from .execution_path import build_execution_path
from .node_description import build_node_description
from .system_architecture import build_system_architecture
from .system_federation import build_federated_architecture
from .agent_views import AgentViewStore, AgentViewError
from .agent_setup_api import register_agent_setup_routes
from .documentation_config import DocumentationConfig
from .documentation_mcp import DocumentationMCPError, discover_tools, search_documents, validate_arguments
from .semantic_service import SemanticService
from .wiki_service import WikiService
from .semantic_embeddings import SemanticIndexError
from .architecture_api import register_architecture_routes
from .coverage_api import register_coverage_routes
from .graph_analysis_api import register_graph_analysis_routes
from .semantic_api import register_semantic_routes
from .documentation_api import register_documentation_routes
from .data_store import DataMapStore
from .data_api import register_data_routes
from .document_service import DocumentService
from .document_api import register_document_routes
from .document_search import DocumentSearch
from .document_search_api import register_document_search_routes
from .document_retention_api import register_document_retention_routes
from .reference_service import ReferenceService
from .reference_api import register_reference_routes
from .code_patterns import PatternCatalog
from .checkout_maps_api import create_checkout_maps, register_checkout_routes
from .change_review_api import register_change_review_routes
from .module_proposal_api import register_module_proposal_routes
from .module_proposal_service import ModuleProposalService
from .core_generations import GenerationError
from .pattern_catalog_api import register_pattern_routes
from .module_analysis_service import ModuleAnalysisService
from .module_analysis_api import register_module_analysis_routes
from .debug_feedback_api import register_debug_feedback_routes
from .library_service import LibraryService
from .library_api import register_library_routes

from .mod_api import register_mod_routes
from .color_scheme_api import register_color_scheme_routes
from .mod_grouping import catalog as mod_grouping_catalog
from .code_health_service import CodeHealthService
from .code_health_refresh import CodeHealthRefresh
from .code_health_api import register_code_health_routes
from .research.service import ResearchService
from .research.api import register_research_routes

ROOT = resource_root()


def create_app(data_dir: Path | None = None, *, module_generations=None, desktop_automation=None) -> FastAPI:
    """
    Create an isolated application after recovering only explicitly confirmed cleanup transactions.

    Args:
        data_dir: Optional explicit application storage root; None selects the existing runtime default.
        module_generations: Optional trusted read-only compute-generation owner. None preserves
            inline execution and starts no compute process. The application closes this owner
            during lifespan shutdown; stores and transport remain in this stable host.
    Returns:
        FastAPI application with a shared HTTP/worker map-maintenance boundary and lifecycle-owned services.
    Raises:
        CleanupError: An interrupted cleanup journal cannot be safely recovered; unknown paths are preserved.
    """
    from .map_cleanup_journal import recover as recover_map_cleanup
    from .map_maintenance import MapMaintenanceHTTP, guard_executor, maintenance_route_class
    from .map_cleanup import MapCleanup
    from .map_cleanup_api import register_map_cleanup_routes
    storage_root = Path(data_dir or default_data_dir()).resolve()
    recovered_cleanup = recover_map_cleanup(storage_root)
    store = ProjectStore(storage_root)
    from .browser_sessions import BrowserSessions
    from .browser_api import register_browser_routes
    browser_sessions = BrowserSessions()
    from .analysis_contexts import AnalysisContexts
    analysis_contexts = AnalysisContexts()
    checkout_maps = create_checkout_maps(store.data_dir, store.graphify_runtime.selected)
    activity = ActivityJournal(store.data_dir)
    technical_logs = TechnicalJournal(store.data_dir)
    from .release_updates import ReleaseUpdates
    release_updates = ReleaseUpdates(store.data_dir)
    data_maps = DataMapStore(store.data_dir, maintenance=store.maintenance)
    documents = DocumentService(store)
    semantic = SemanticService(store)
    document_search = DocumentSearch(store.data_dir, semantic.embedder)
    references = ReferenceService(store.data_dir, semantic.embedder, ROOT / "resources" / "language-references",
                                  ROOT / "resources" / "reference-search")
    libraries = LibraryService(store, semantic.embedder)
    wiki = WikiService(store, semantic)
    research = ResearchService(store, references, libraries)
    health_refresh = CodeHealthRefresh(store)
    from .semantic_enrichment import SemanticEnrichment
    from .semantic_enrichment_api import register_semantic_enrichment_routes
    engine_enrichment = SemanticEnrichment(store)
    store.on_map_used = engine_enrichment.use

    def snapshot_published(project_id, snapshot_id):
        """Independent derived queues must not prevent one another or roll back a published map."""
        import logging
        for callback in (health_refresh.snapshot_published, semantic.snapshot_published):
            try:
                callback(project_id, snapshot_id)
            except Exception:
                logging.getLogger(__name__).exception('Snapshot follow-up could not be queued')

    store.on_snapshot_published = snapshot_published

    remote_repositories = RemoteRepositories(store, managed_repositories_dir(store.data_dir))
    for service in (store, health_refresh, semantic, engine_enrichment, engine_enrichment.runtime,
                    remote_repositories, references, libraries, research):
        service.executor = guard_executor(service.executor, store.maintenance)
    cleanup = MapCleanup(store, data_maps, store.maintenance, recovered=recovered_cleanup,
                         protected_roots=lambda: [item['root'] for item in documents.bindings.values()],
                         invalidate=analysis_contexts.invalidate_projects)
    http_state = HTTPState((data_maps.request_stop, remote_repositories.request_stop,
                            references.request_stop, research.request_stop))

    @asynccontextmanager
    async def lifespan(app):
        """Запускает первичный индекс справочников; завершение ждёт локальные workers."""
        technical_logs.install()
        browser_sessions.start()
        analysis_contexts.start()
        checkout_maps.start()
        release_updates.start()
        try:
            await asyncio.to_thread(references.start_initial_indexing)
            yield
        finally:
            http_state.request_stop()
            release_updates.close()
            failure = None
            shutdown_services = (("temporary checkout maps", checkout_maps), ("map cleanup", cleanup), ("temporary analysis contexts", analysis_contexts), ("temporary browser sessions", browser_sessions), ("optional semantic engines", engine_enrichment), ("agent activity", activity), ("health refresh", health_refresh), ("research workflows", research), ("remote repositories", remote_repositories),
                                  ("document search", document_search), ("documents", documents), ("project store", store), ("semantic index", semantic), ("data maps", data_maps),
                                  ("language references", references), ("library documentation", libraries), ("technical logs", technical_logs))
            if module_generations is not None:
                shutdown_services = (("module compute generations", module_generations),) + shutdown_services
            for name, service in shutdown_services:
                try:
                    await asyncio.to_thread(service.close)
                except Exception as error:
                    # Освобождаем остальные ресурсы; последующая ошибка не скрывает первую.
                    if failure is None:
                        failure = error
                    else:
                        failure.add_note(f"Additional shutdown failure ({name}): {error}")
            if failure is not None:
                raise failure

    app = FastAPI(title="Code Atlas", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.router.route_class = maintenance_route_class(store.maintenance)
    app.state.request_shutdown = http_state.request_stop
    app.state.store = store
    app.state.map_cleanup = cleanup
    register_map_cleanup_routes(app, cleanup)
    from .usage_metrics import UsageMiddleware
    app.add_middleware(UsageMiddleware, metrics=store.usage)
    app.state.browser_sessions = browser_sessions
    app.state.analysis_contexts = analysis_contexts
    app.state.checkout_maps = checkout_maps
    register_checkout_routes(app, checkout_maps)
    register_change_review_routes(app, checkout_maps, store.data_dir)
    from .position_context_api import register_position_context_routes
    register_position_context_routes(app, store, checkout_maps)
    register_browser_routes(app, browser_sessions)
    app.state.semantic_enrichment = engine_enrichment
    register_semantic_enrichment_routes(app, engine_enrichment, store)
    app.state.agent_activity = activity
    register_activity_routes(app, activity, store)
    from .desktop_widget import register_desktop_widget
    register_desktop_widget(app, activity)
    from .desktop_automation import register_desktop_automation
    register_desktop_automation(app, desktop_automation)
    app.state.technical_logs = technical_logs
    register_technical_routes(app, technical_logs)
    register_diagnostics_routes(app, (activity, technical_logs), owned_data_dir=store.data_dir, usage=store.usage)
    register_mod_routes(app, store)
    register_color_scheme_routes(app, store.mods)
    app.state.research = research
    register_research_routes(app, research)
    app.state.code_health = CodeHealthService(store, refresh=health_refresh)
    app.state.health_refresh = health_refresh
    register_code_health_routes(app, app.state.code_health)
    register_agent_setup_routes(app, store)
    from .release_update_api import register_release_update_routes
    register_release_update_routes(app, release_updates)
    app.state.data_maps = data_maps
    register_data_routes(app, store, data_maps)
    app.state.documents = documents
    register_document_routes(app, store, documents, data_maps)
    register_document_retention_routes(app, documents)
    app.state.document_search = document_search
    register_document_search_routes(app, store, documents, document_search)
    app.state.semantic = semantic
    app.state.references = references
    register_reference_routes(app, references)
    # PyInstaller exposes bundled data through trusted directory aliases. Resolve
    # this fixed application path; catalogue callers/files retain strict guards.
    app.state.code_patterns = PatternCatalog((ROOT / "resources" / "code-patterns").resolve())
    register_pattern_routes(app, app.state.code_patterns)
    app.state.module_generations = module_generations
    app.state.module_analysis = ModuleAnalysisService(store,
        compute=module_generations.compute if module_generations is not None else None,
        cancellable_compute=module_generations is not None)
    register_module_analysis_routes(app, app.state.module_analysis)
    app.state.module_proposals = ModuleProposalService(store, analysis_contexts, slots=app.state.module_analysis.slots)
    register_module_proposal_routes(app, app.state.module_proposals)
    @app.exception_handler(GenerationError)
    async def generation_error(request, error):
        """Report explicit worker lifecycle failures without inline fallback or replay."""
        return JSONResponse({'error': str(error), 'code': error.code}, status_code=error.status)
    register_debug_feedback_routes(app, store)
    app.state.libraries = libraries
    register_library_routes(app, libraries)
    app.state.wiki = wiki
    register_coverage_routes(app, store)
    register_architecture_routes(app, store)
    app.state.remote_repositories = remote_repositories
    agent_views = AgentViewStore(store.data_dir)
    app.state.agent_views = agent_views
    mcp_slots = asyncio.Semaphore(2)
    documentation_config = DocumentationConfig(store.data_dir)
    app.state.documentation_config = documentation_config
    # Обёртки сохраняют заменяемость адаптеров в существующих интеграционных тестах,
    # включая замену после создания приложения; feature-модули не импортируют server.
    register_graph_analysis_routes(
        app, store,
        entrypoints_builder=lambda *args: build_entrypoints(*args),
        federation_builder=lambda *args: build_federated_architecture(*args),
        systems_builder=lambda *args: build_system_architecture(*args),
        description_builder=lambda *args: build_node_description(*args),
        description_reads_shared_graph=True,
        path_builder=lambda *args: build_execution_path(*args),
        comparison_builder=lambda *args: compare_graph(*args),
        source_reader=lambda *args: read_node_source(*args),
    )
    register_semantic_routes(app, semantic, wiki)
    register_documentation_routes(
        app, documentation_config,
        discover=lambda *args: discover_tools(*args),
        search=lambda *args: search_documents(*args),
        validate=lambda *args: validate_arguments(*args),
    )

    app.add_middleware(ActivityObserver, journal=activity)
    app.add_middleware(LocalHTTPBoundary)
    app.add_middleware(MapMaintenanceHTTP, maintenance=store.maintenance)
    app.add_middleware(HTTPLifecycle, state=http_state)
    register_map_export_routes(app)

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        """Передаёт ожидаемую ошибку интерфейсу без traceback."""
        payload = {"error": str(error)}
        if getattr(error, 'code', None):
            payload['code'] = error.code
            if hasattr(error, 'job_id'):
                payload['jobId'] = error.job_id
        return JSONResponse(payload, status_code=error.status)

    @app.exception_handler(SemanticIndexError)
    async def semantic_index_error(request, error):
        """Возвращает ожидаемую ошибку производного индекса без traceback в UI."""
        return JSONResponse({"error": str(error)}, status_code=error.status)

    @app.exception_handler(AgentViewError)
    async def agent_view_error(request, error):
        """Передаёт безопасную ошибку immutable-вида без внутренних путей."""
        return JSONResponse({"error": str(error)}, status_code=error.status)

    @app.exception_handler(DocumentationMCPError)
    async def documentation_error(request, error):
        """Возвращает безопасное сообщение MCP без текста серверного исключения."""
        return JSONResponse({"error": str(error)}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        """Описывает неверный JSON без отражения входных данных."""
        return JSONResponse({"error": "Проверьте поля запроса и формат JSON"}, status_code=422)

    @app.get("/api/views")
    def list_agent_views():
        """Перечисляет сохранённые виды независимо от состояния исходных проектов."""
        return agent_views.list()

    @app.post("/api/views")
    def create_agent_view(body: AgentViewInput):
        """Атомарно закрепляет актуальный снимок и сохраняет независимую выборку."""
        with store.lock:
            project_path, graph = store.comparison_snapshot(body.projectId)
            store.assert_snapshot(body.projectId, body.snapshotId)
            return agent_views.create(project_path, graph, body.model_dump())

    @app.get("/api/views/{view_id}")
    def get_agent_view(view_id: str):
        """Возвращает сохранённые факты и layout без обращения к текущему графу."""
        return agent_views.get(view_id)

    @app.get("/api/views/{view_id}/image.png")
    def agent_view_image(view_id: str):
        """Рендерит сохранённое расположение локальным изолированным PNG-worker."""
        from .snapshot_png import render_png
        view = agent_views.get(view_id)
        try:
            path = render_png(view, store.data_dir)
        except ValueError:
            raise AgentViewError("Сохранённый layout не подходит для PNG.", 422) from None
        except RuntimeError:
            raise AgentViewError("Локальный PNG-renderer недоступен или не завершил изображение.", 503) from None
        return FileResponse(path, media_type="image/png", filename=f"code-atlas-{view_id}.png")

    @app.post("/api/projects/{project_id}/search-nodes")
    def agent_search_nodes(project_id: str, body: AgentSearchInput):
        """Находит raw-ID в закреплённом снимке для явного последующего выбора."""
        return store.search_project_nodes(project_id, body.snapshotId, body.query, body.limit, body.excludeKinds)

    @app.get("/api/health")
    def health():
        """Показывает установленную версию и режим исполнения."""
        return {"status": "ok", "graphifyVersion": store.graphify_runtime.info().get("version"), "offline": True}

    @app.get("/api/grouping-algorithms")
    def grouping_algorithms():
        """Перечисляет доступные локальные провайдеры для выбора в интерфейсе."""
        return {"algorithms": [*list_grouping_algorithms(), *mod_grouping_catalog(store.mods)], "default": DEFAULT_ALGORITHM}

    @app.get("/api/projects")
    def projects():
        """Возвращает ранее добавленные проекты."""
        return store.list_projects()

    @app.post("/api/remote-repositories", status_code=202)
    def clone_remote_repository(body: RemoteRepositoryInput):
        """Получает удалённый репозиторий в фоне; не запускает его исходный код."""
        return remote_repositories.submit(body.url, body.ref)

    @app.get("/api/remote-repositories/jobs/{job_id}")
    def remote_repository_job(job_id: str):
        """Возвращает этап клонирования и последующей индексации."""
        return remote_repositories.get(job_id)

    @app.post("/api/projects", status_code=202)
    def add_project(body: ProjectInput):
        """Принимает путь и запускает асинхронную сборку."""
        return store.add(body.path)

    @app.post("/api/projects/{project_id}/rebuild", status_code=202)
    def rebuild(project_id: str):
        """Запускает новую ревизию карты."""
        return store.rebuild(project_id)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        """Возвращает состояние фоновой задачи."""
        return store.get_job(job_id)

    register_progressive_map_routes(app, store)

    @app.get("/api/projects/{project_id}/graph")
    def graph(project_id: str, algorithm: str = DEFAULT_ALGORITHM,
              projection: str = Query("full", pattern="^(full|map)$")):
        """Отдаёт сохранённый граф; карта запрашивает проекцию без подробных attributeFacts."""
        payload = serialize_graph(store.get_graph(project_id, algorithm), projection)
        return Response(content=payload, media_type="application/json")

    async def mcp_request(project_id, snapshot_id, question=None, node_id=None):
        """Ограничивает число MCP-процессов и возвращает проверяемое происхождение ответа."""
        graph_path = await asyncio.to_thread(store.mcp_snapshot, project_id, snapshot_id, node_id)
        try:
            async with asyncio.timeout(45):
                async with mcp_slots:
                    if node_id is not None:
                        with store.graphify_runtime.activate():
                            result = await describe_node(graph_path, node_id)
                    else:
                        with store.graphify_runtime.activate():
                            result = await query_graph(graph_path, question)
                    return {**result, "snapshotId": snapshot_id}
        except TimeoutError as error:
            raise StoreError("Локальный MCP не ответил вовремя", 504) from error
        except Exception as error:
            raise StoreError(f"Ошибка локального MCP: {str(error)[:1000]}", 502) from error

    @app.post("/api/projects/{project_id}/describe")
    async def describe(project_id: str, body: NodeInput):
        """Запрашивает узел и соседей через настоящий stdio MCP Graphify."""
        return await mcp_request(project_id, body.snapshotId, node_id=body.nodeId)

    @app.post("/api/projects/{project_id}/query")
    async def query(project_id: str, body: QueryInput):
        """Запрашивает контекст графа локально, без генеративной модели."""
        return await mcp_request(project_id, body.snapshotId, question=body.question)

    app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
    return app
