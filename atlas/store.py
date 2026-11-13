"""Хранение проектов и последовательная фоновая сборка неизменяемых снимков."""

from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from pathlib import Path
from threading import Lock, RLock
from uuid import uuid4

from .graphify_adapter import build_graph
from .integrations.graphify.runtime import GraphifyRuntime
from .enrichment_pipeline import enrich_snapshot
from .mod_analysis import active_analysis_rules, assert_analysis_rules_current
from .project_file_inventory import project_file_inventory
from .health_snapshot_overlay import apply_overlay, overlay_stamp
from .mod_store import ModStore, ModError
from .mod_grouping import resolve as mod_grouping_provider
from .grouping import GROUPING_VERSION, enrich_graph
from .grouping_providers import DEFAULT_ALGORITHM, get_provider
from .graphistry_provider import GraphistryError
from .grouping_cache import cache_path, read_cached_graph, source_stamp
from .health_payload_codec import encode_health_payloads, decode_health_payloads
from .health_snapshot_overlay import rebase_health
from .mcp_graph import build_mcp_graph
from .graph_context import read_graph_node, read_graph_neighbors
from .project_storage import snapshot_directory, temporary_extraction_cache
from .semantic_overlay import SemanticOverlayReader
from .snapshot_objects import write_snapshot, read_snapshot
from .progressive_map import publish_progressive_map
from .map_maintenance import MapMaintenance, guard_executor, map_operation


MAX_PUBLISHED_PROJECTS = 8
# Бюджет измеряется размером JSON на диске, а не RSS Python-процесса.
MAX_PUBLISHED_SOURCE_BYTES = 128 * 1024 * 1024
MAX_OVERVIEW_CONTEXTS = 8
# Account serialized bounded evidence, never full graphs or process RSS.
MAX_OVERVIEW_CACHE_BYTES = 4 * 1024 * 1024


class StoreError(Exception):
    """Ошибка пользовательского запроса с HTTP-статусом."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def write_json(path: Path, value: dict):
    """Атомарно заменяет JSON: незавершённая запись не портит прошлый снимок."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # CLI exports may derive the same MCP file while the server also requests it.
    # Each writer needs its own staging file before the atomic replacement.
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ProjectStore:
    """Один процесс приложения владеет каталогом данных и одной очередью сборки."""

    def __init__(self, data_dir: Path):
        """
        Create the registered project store and its queue-tracked maintenance boundary.

        Args:
            data_dir: Explicit owned application storage root; source repositories remain outside it.
        """
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry = self.data_dir / "projects.json"
        self.lock = RLock()
        self.maintenance = MapMaintenance()
        self.grouping_lock = Lock()
        self.jobs = {}
        self.active_job = None
        from .usage_metrics import UsageMetrics
        self.usage = UsageMetrics()
        self.mods = ModStore(self.data_dir, usage=self.usage)
        self.graphify_runtime = GraphifyRuntime(self.data_dir)
        self.executor = guard_executor(ThreadPoolExecutor(max_workers=1, thread_name_prefix="graphify"), self.maintenance)
        self.projects = self._load_projects()
        # Один разобранный опубликованный снимок на проект. Ключ включает stamp файла,
        # чтобы перезапись той же ревизии не отдавала старый разбор.
        self._published_graph: OrderedDict[str, tuple[str, list[int], dict]] = OrderedDict()
        self._search_facts_key = None
        self._search_facts = None
        self.overview_lock = Lock()
        self._overview_cache = OrderedDict()
        self._overview_cache_bytes = 0
        self.on_snapshot_published = None
        self.semantic_overlay = SemanticOverlayReader()

    def _load_projects(self) -> dict:
        """Восстанавливает реестр; прерванные сборки отмечает как ошибочные."""
        if not self.registry.exists():
            return {}
        try:
            projects = json.loads(self.registry.read_text(encoding="utf-8"))
            if not isinstance(projects, dict):
                raise ValueError("ожидается объект")
            for project_id, project in projects.items():
                if not isinstance(project, dict) or project.get("id") != project_id:
                    raise ValueError("неверная запись проекта")
                if project.get("status") in {"queued", "running"}:
                    project["status"] = "failed"
                    project["error"] = "Приложение остановилось во время сборки. Запустите её снова."
            return projects
        except (ValueError, OSError) as error:
            raise RuntimeError(f"Не удалось прочитать реестр {self.registry}: {error}") from error

    def _public(self, project: dict) -> dict:
        """
        Исключает внутренние пути к снимкам и добавляет id опубликованной ревизии.

        Args:
            project: Внутренняя регистрация; наружу не выходят поля с префиксом _.
        Returns:
            Независимый словарь публичных скалярных полей и доступности снимка.
        """
        public = {key: value for key, value in project.items() if not key.startswith("_")}
        graph_path = project.get("_graph")
        public["snapshotId"] = Path(graph_path).parent.name if graph_path else None
        public["snapshotAvailable"] = bool(graph_path and Path(graph_path).is_file())
        public.setdefault('analysisStatus', 'unknown')
        return public

    def list_projects(self) -> dict:
        """Возвращает согласованный снимок списка проектов и текущей сборки."""
        with self.lock:
            projects = [self._public(item) for item in self.projects.values()]
            active = self.jobs.get(self.active_job)
            return deepcopy({"projects": projects, "activeJob": active})

    def _project(self, project_id: str) -> dict:
        """Находит проект по ID, никогда не используя ID как файловый путь."""
        if project_id not in self.projects:
            raise StoreError("Проект не найден", 404)
        return self.projects[project_id]

    @map_operation
    def add(self, path_text: str) -> dict:
        """Регистрирует абсолютный путь и запускает первый анализ в фоне."""
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            raise StoreError("Укажите абсолютный путь к каталогу проекта")
        if not path.is_dir():
            raise StoreError("Каталог проекта не существует или недоступен")
        path = path.resolve()
        if path == self.data_dir or self.data_dir in path.parents:
            raise StoreError("Каталог данных приложения нельзя анализировать как проект")
        project_id = sha256(str(path).encode()).hexdigest()[:16]
        with self.lock:
            if self.active_job:
                raise StoreError("Дождитесь завершения текущей сборки", 409)
            if project_id not in self.projects:
                self.projects[project_id] = {
                    "id": project_id, "name": path.name, "path": str(path),
                    "status": "queued", "nodeCount": 0, "edgeCount": 0,
                }
            job = self._submit(project_id)
            return {"project": self._public(self.projects[project_id]), "job": job}

    @map_operation
    def rebuild(self, project_id: str) -> dict:
        """Обновляет карту; предыдущий успешный снимок доступен до публикации нового."""
        with self.lock:
            self._project(project_id)
            if self.active_job:
                raise StoreError("Дождитесь завершения текущей сборки", 409)
            return {"job": self._submit(project_id)}

    def _submit(self, project_id: str) -> dict:
        """Сохраняет состояние перед постановкой задачи в единственный worker."""
        job_id = uuid4().hex
        job = {"id": job_id, "projectId": project_id, "status": "queued"}
        project = self.projects[project_id]
        project.update(status="queued", error=None)
        write_json(self.registry, self.projects)
        self.jobs[job_id] = job
        self.active_job = job_id
        self.executor.submit(self._build, job_id)
        return deepcopy(job)

    def _build(self, job_id: str):
        """
        Собирает отдельную ревизию, затем атомарно публикует только успешную.

        Args:
            job_id: ID уже зарегистрированной задачи текущего worker.
        Returns:
            None. Завершённая частичная карта публикуется с analysisStatus=partial;
            исключение сохраняет прежнюю карту и переводит задачу в failed.
        """
        with self.lock:
            job = self.jobs[job_id]
            project = self.projects[job["projectId"]]
            job["status"] = project["status"] = "running"
            project_path = Path(project["path"])
        output = None
        try:
            output = snapshot_directory(self.data_dir, project['id'], job_id)
            inventory = project_file_inventory(project_path)
            analysis_rules = active_analysis_rules(self.mods, inventory=inventory)
            with temporary_extraction_cache(output), self.graphify_runtime.activate():
                graph = build_graph(project_path, output)
            from .usage_metrics import observe
            for mod in analysis_rules.get('mods', []):
                observe(self.usage, 'rule_pass', mod['id'])
            graph = enrich_snapshot(graph, project_path, analysis_rules=analysis_rules, file_inventory=inventory)
            graph = enrich_graph(graph)
            graph["metadata"]["snapshotId"] = job_id
            graph_path = output / "atlas-graph.json"
            mcp_path = output / "mcp-graph.json"
            # The canonical graph contains every fact. Graphify's larger compatible
            # projection is derived only when an actual Graphify client requests it.
            write_snapshot(graph_path, encode_health_payloads(graph))
            publish_progressive_map(self.data_dir, graph_path, graph)
            with self.lock:
                # Неуспешная сборка сюда не попадает и не сбрасывает прошлый разбор.
                assert_analysis_rules_current(self.mods, analysis_rules, inventory=inventory)
                self._published_graph.pop(project["id"], None)
                project.update(
                    status="completed", error=None,
                    **{key: graph.get('metadata', {}).get('analysisDiagnostics', {}).get(key, default)
                       for key, default in (('analysisStatus', 'unknown'), ('errorCount', 0),
                                            ('failedFileCount', None), ('excludedDataCount', None))},
                    programNodeCount=len(graph['nodes']) - graph.get('metadata', {}).get('diagnosticNodeCount', 0),
                    diagnosticNodeCount=graph.get('metadata', {}).get('diagnosticNodeCount', 0),
                    nodeCount=len(graph["nodes"]), edgeCount=len(graph["edges"]),
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    _graph=str(graph_path), _rawGraph=graph["metadata"]["rawGraphPath"],
                    _mcpGraph=str(mcp_path), _mcpGraphLazy=True,
                )
                job["status"] = "completed"
                job['analysisStatus'] = project['analysisStatus']
                job['errorCount'] = project['errorCount']
                job['followupsPending'] = bool(self.on_snapshot_published)
        except Exception as error:
            with self.lock:
                message = str(error)[-4000:] or type(error).__name__
                project.update(status="failed", error=message)
                job.update(status="failed", error=message)
        finally:
            with self.lock:
                try:
                    write_json(self.registry, self.projects)
                except OSError as error:
                    message = f"Не удалось сохранить реестр: {error}"
                    project.update(status="failed", error=message)
                    job.update(status="failed", error=message)
                self.active_job = None
                # История текущей сессии ограничена; снимки на диске сохраняются.
                while len(self.jobs) > 100:
                    self.jobs.pop(next(iter(self.jobs)))
        if job["status"] == "completed" and self.on_snapshot_published:
            try:
                self.on_snapshot_published(project["id"], job_id)
            except Exception:
                # Производный поиск не должен отменять успешную публикацию карты.
                pass
            finally:
                with self.lock:
                    job['followupsPending'] = False

    def get_job(self, job_id: str) -> dict:
        """Возвращает прогресс конкретной сборки текущей сессии."""
        with self.lock:
            if job_id not in self.jobs:
                raise StoreError("Сборка не найдена. Обновите список проектов.", 404)
            return deepcopy(self.jobs[job_id])

    def _snapshot_path(self, project_id: str, field: str) -> Path:
        """Проверяет наличие снимка и границу каталога перед чтением."""
        with self.lock:
            project = self._project(project_id)
            value = project.get(field)
        if not value:
            raise StoreError("Карта ещё не построена", 409)
        path = Path(value).resolve()
        if not path.is_relative_to(self.data_dir) or not path.is_file():
            raise StoreError("Снимок карты отсутствует. Перестройте проект.", 409)
        return path

    @map_operation
    def get_graph(self, project_id: str, algorithm: str = DEFAULT_ALGORITHM) -> dict:
        """
        Возвращает группировку существующей карты или запускает восстановление потерянного снимка.

        Args:
            project_id: Явно открываемый текущий проект.
            algorithm: Зарегистрированная группировка опубликованного графа.
        Returns:
            Граф существующего снимка; намеренно очищенная карта требует ручной сборки.
        Raises:
            SnapshotRecoveryPending: Одна попытка восстановления поставлена в очередь,
                выполняется или уже завершилась ошибкой; HTTP409 содержит code и jobId.
            StoreError: Неизвестный проект/алгоритм, небезопасный путь или отсутствие карты.
        """
        try:
            mod_grouping_provider(self.mods, algorithm) if algorithm.startswith("mod.") else get_provider(algorithm)
        except ValueError as error:
            raise StoreError(str(error), 400) from error
        from .snapshot_recovery import ensure_current_snapshot
        ensure_current_snapshot(self, project_id)
        path = self._snapshot_path(project_id, "_graph")
        # Готовая производная уже содержит исходные узлы и связи: повторно
        # разбирать большой raw JSON на каждом тёплом запросе не требуется.
        health_stamp = overlay_stamp(path)
        graph = read_cached_graph(path, algorithm)
        if graph is None:
            with self.lock:
                path = self._snapshot_path(project_id, "_graph")
                health_stamp = overlay_stamp(path)
                graph = deepcopy(self._base_snapshot(project_id, path))
            if (graph["metadata"].get("groupingVersion") != GROUPING_VERSION
                    or graph["metadata"].get("groupingProvider") != algorithm):
                graph = self._grouped_graph(path, graph, algorithm, health_stamp)
        graph["metadata"]["snapshotId"] = path.parent.name
        graph["metadata"].pop("rawGraphPath", None)
        with self.lock:
            if overlay_stamp(path) != health_stamp:
                # A grouped cache can contain an earlier overlay. A delta is
                # relative to captured bytes, so it must never patch that stale base.
                captured = self._published_snapshot(project_id, path)
                graph = rebase_health(graph, captured)
            else:
                graph = apply_overlay(graph, path)
            graph = self.semantic_overlay.project(graph, path, self.mods)
        callback = getattr(self, 'on_map_used', None)
        if callback is not None:
            try:
                status = callback(project_id, path.parent.name)
                graph = {**graph, 'metadata': {**graph['metadata'], 'semanticEngines': status}}
            except (StoreError, ValueError):
                # Optional discovery cannot prevent opening an existing map.
                pass
        return graph

    def _grouped_graph(self, path: Path, graph: dict, algorithm: str, health_stamp=None) -> dict:
        """Кэширует производную раскладку отдельно от неизменяемого исходного снимка.

        Отдельная блокировка предотвращает дублирующие расчёты, не задерживая
        чтение реестра, публикацию другой ревизии и запросы к MCP.
        """
        with self.grouping_lock:
            # Другой запрос мог завершить расчёт, пока этот ожидал блокировку.
            cached = read_cached_graph(path, algorithm)
            if cached is not None:
                return cached
            stamp = source_stamp(path)
            acquired_health = tuple(health_stamp) if health_stamp is not None else overlay_stamp(path)
            try:
                if algorithm.startswith("mod."):
                    result = enrich_graph(graph, algorithm, provider_override=mod_grouping_provider(self.mods, algorithm))
                else:
                    result = enrich_graph(graph, algorithm)
            except (GraphistryError, ModError) as error:
                raise StoreError(str(error), 503) from error
            with self.lock:
                publish = source_stamp(path) == stamp and overlay_stamp(path) == acquired_health
            if publish:
                # Serialize outside the global lock. A later refresh may make
                # this old-stamped cache obsolete, but readers will reject it.
                write_json(cache_path(path, algorithm), {"sourceStamp": stamp, 'overlayStamp': list(acquired_health), "graph": result})
            return result

    def _remember_graph(self, project_id: str, snapshot_path: Path) -> dict:
        """Возвращает копию опубликованного снимка той же ревизии без повторного чтения файла.

        Вызывающий уже держит `self.lock`. Копия нужна, потому что маршруты дописывают
        metadata. Битый JSON не запоминается: следующий вызов снова читает диск и снова
        отвечает 409. Это не кэш группировки и не кэш рабочей копии.
        """
        return deepcopy(self._published_snapshot(project_id, snapshot_path))

    def _published_snapshot(self, project_id: str, snapshot_path: Path) -> dict:
        """Return the same optional semantic projection to every pinned graph consumer."""
        graph = self._base_snapshot(project_id, snapshot_path)
        return self.semantic_overlay.project(graph, snapshot_path, self.mods)

    def _base_snapshot(self, project_id: str, snapshot_path: Path) -> dict:
        """Закрепляет внутренний снимок без копирования; вызывающий держит `self.lock`.

        Возвращённый граф принадлежит хранилищу и не должен изменяться или выходить
        наружу. Точечные читатели копируют только выбранные факты; читатели полного
        графа используют `_remember_graph`, сохраняя изоляцию своих изменений.
        """
        snapshot_id = snapshot_path.parent.name
        try:
            stamp = [*source_stamp(snapshot_path), *overlay_stamp(snapshot_path)]
        except (OSError, ValueError, TypeError) as error:
            raise StoreError('Не удалось прочитать карту. Перестройте проект.', 409) from error
        cached = self._published_graph.get(project_id)
        if cached is not None and cached[0] == snapshot_id and cached[1] == stamp:
            self._published_graph.move_to_end(project_id)
            return cached[2]
        self._published_graph.pop(project_id, None)
        graph = self._read_graph(snapshot_path)
        graph["metadata"]["snapshotId"] = snapshot_id
        apply_overlay(graph, snapshot_path)
        self._retain_published(project_id, snapshot_id, stamp, graph)
        return graph

    def _retain_published(self, project_id: str, snapshot_id: str, stamp: list[int], graph: dict) -> None:
        """Удерживает недавние снимки в пределах числа проектов и суммарного размера JSON.

        Вызывающий держит `self.lock`. Крупный одиночный снимок остаётся доступным
        текущему запросу, но не вытесняет все остальные и не сохраняется в памяти.
        Вытеснение не удаляет опубликованные файлы; следующий запрос читает их снова.
        """
        size = stamp[1] + (stamp[3] if len(stamp) > 3 else 0)
        if size > MAX_PUBLISHED_SOURCE_BYTES:
            return
        retained_size = sum(entry[1][1] + (entry[1][3] if len(entry[1]) > 3 else 0) for entry in self._published_graph.values())
        while self._published_graph and (len(self._published_graph) >= MAX_PUBLISHED_PROJECTS
                                         or retained_size + size > MAX_PUBLISHED_SOURCE_BYTES):
            _, removed = self._published_graph.popitem(last=False)
            retained_size -= removed[1][1] + (removed[1][3] if len(removed[1]) > 3 else 0)
        self._published_graph[project_id] = (snapshot_id, stamp, graph)

    def _read_graph(self, path: Path) -> dict:
        """
        Читает legacy JSON или manifest и проверяет полный исходный граф.

        Manifest-объекты проверяются по хэшу и бюджетам до восстановления DTO;
        повреждение, недоступный объект или неверная схема дают StoreError(409).
        Алгоритмы представления и анализ исходников здесь не запускаются.
        """
        try:
            graph = decode_health_payloads(read_snapshot(path))
            if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
                raise ValueError("неверный формат графа")
            if not isinstance(graph.get("metadata"), dict):
                raise ValueError("неверный формат метаданных")
            for node in graph["nodes"]:
                if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                    raise ValueError("неверный формат узла")
            for edge in graph["edges"]:
                if not isinstance(edge, dict) or not isinstance(edge.get("source"), str) or not isinstance(edge.get("target"), str):
                    raise ValueError("неверный формат связи")
            return graph
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise StoreError("Не удалось прочитать карту. Перестройте проект.", 409) from error

    @map_operation
    def raw_graph(self, project_id: str) -> Path:
        """Возвращает неизменённый исходный результат извлечения Graphify."""
        return self._snapshot_path(project_id, "_rawGraph")

    @map_operation
    def published_mcp_graph(self, project_id: str) -> Path:
        """Закрепляет последнюю общую карту для экспортируемой stdio-конфигурации."""
        with self.lock:
            snapshot_path = self._snapshot_path(project_id, "_graph")
            return self._mcp_graph_path(project_id, snapshot_path)

    @map_operation
    def project_overview(self, project_id: str, snapshot_id: str | None = None) -> dict:
        """
        Читает холодный обзор вне registry lock и переиспользует ограниченное свидетельство ревизии.

        Args:
            project_id: Существующая регистрация проекта.
            snapshot_id: Необязательная ожидаемая ревизия; другая ревизия отвергается.
        Returns:
            Независимый DTO; состояние задачи актуально, health overlay не меняет статический обзор.
            Вызывающий не должен удерживать self.lock: ожидание overview_lock всегда идёт вне неё.
        Raises:
            StoreError: Неизвестная регистрация (404), смена ревизии/источника (409) или слишком большой DTO (413).
        """
        from .project_overview import compose_project_overview, snapshot_overview
        project, path, job = self._overview_scope(project_id, snapshot_id)
        if path is None:
            return compose_project_overview(project, None, job)
        # No caller waits for this lock while holding the registry lock. The
        # separate serial cold path prevents duplicate hydrations without
        # delaying list_projects/get_job or pinning a full graph in their LRU.
        with self.overview_lock:
            before = self._overview_source_stamp(path)
            key = project_id, str(path), tuple(before)
            cached = self._overview_cache.get(key)
            size = 0
            if cached is not None:
                self._overview_cache.move_to_end(key)
                captured = cached[0]
            else:
                try:
                    graph = self._read_graph(path)
                except StoreError:
                    captured = None
                else:
                    captured = deepcopy(snapshot_overview(graph, project['snapshotId']))
                    del graph
                    size = len(json.dumps(captured, ensure_ascii=False).encode('utf-8'))
                    if size > MAX_OVERVIEW_CACHE_BYTES:
                        raise StoreError('Обзор превышает бюджет сохранённых свидетельств.', 413)
            if self._overview_source_stamp(path) != before:
                raise StoreError('Снимок изменился во время чтения обзора. Загрузите его заново.', 409)
            current, current_path, job = self._overview_scope(project_id, project['snapshotId'])
            if current_path != path:
                raise StoreError('Путь снимка изменился во время чтения обзора.', 409)
            if self._overview_source_stamp(path) != before:
                raise StoreError('Источник изменился во время закрепления обзора.', 409)
            if cached is None and captured is not None:
                while self._overview_cache and (len(self._overview_cache) >= MAX_OVERVIEW_CONTEXTS
                        or self._overview_cache_bytes + size > MAX_OVERVIEW_CACHE_BYTES):
                    _, (_, retired_bytes) = self._overview_cache.popitem(last=False)
                    self._overview_cache_bytes -= retired_bytes
                self._overview_cache[key] = captured, size
                self._overview_cache_bytes += size
            return deepcopy(compose_project_overview(current, captured, job))

    def _overview_source_stamp(self, path: Path) -> list[int]:
        """
        Проверяет идентичность канонического источника без чтения тела графа или health overlay.

        Args:
            path: Ранее закреплённый безопасный путь к снимку.
        Returns:
            Stamp manifest и его объектов либо legacy mtime/size.
        Raises:
            StoreError: Источник исчез, повреждён или стал недоступен (409).
        """
        try:
            return source_stamp(path)
        except (OSError, ValueError, TypeError) as error:
            raise StoreError('Источник обзора недоступен или изменился.', 409) from error

    def _overview_scope(self, project_id: str, snapshot_id: str | None) -> tuple[dict, Path | None, dict | None]:
        """
        Закрепляет только небольшие поля регистрации и задачи под короткой registry lock.

        Args:
            project_id: Существующая регистрация проекта.
            snapshot_id: Ожидаемая ревизия либо None при первом текущем запросе.
        Returns:
            Публичная регистрация, безопасный доступный путь либо None и независимые поля задачи.
        Raises:
            StoreError: Регистрация неизвестна (404) или ревизия сменилась (409).
        """
        with self.lock:
            project = self._public(self._project(project_id))
            if snapshot_id is not None and project.get('snapshotId') != snapshot_id:
                raise StoreError('Карта обновилась. Загрузите актуальный обзор.', 409)
            path = None
            if project.get('snapshotAvailable'):
                try:
                    path = self._snapshot_path(project_id, '_graph')
                except StoreError:
                    project['snapshotAvailable'] = False
            job = next((item for item in reversed(list(self.jobs.values()))
                        if item.get('projectId') == project_id), None)
            return project, path, deepcopy(job)

    @map_operation
    def captured_entrypoint_snapshot(self, project_id: str, snapshot_id: str) -> dict:
        """Read a pinned entry catalogue without cloning the full graph or scanning source.

        Only the small returned DTO is copied. Neither the cached graph nor its
        nested metadata may escape the store lock into a mutable API response.
        """
        from .captured_entrypoints import captured_entrypoint_catalog
        with self.lock:
            path = self._snapshot_path(project_id, '_graph')
            if path.parent.name != snapshot_id:
                raise StoreError('Карта обновилась. Загрузите актуальный снимок перед просмотром входов.', 409)
            graph = self._published_snapshot(project_id, path)
            return deepcopy(captured_entrypoint_catalog(graph, snapshot_id))

    @map_operation
    def test_dataset_snapshot(self, project_id: str, snapshot_id: str, node_id: str,
                              source_id: str | None = None, cursor: str | None = None, limit: int = 20) -> dict:
        """Copy one bounded dataset page under the snapshot lock without cloning a full map."""
        from .test_dataset_page import read_test_dataset_page
        with self.lock:
            path = self._snapshot_path(project_id, '_graph')
            if path.parent.name != snapshot_id:
                raise StoreError('Карта обновилась. Загрузите актуальный снимок.', 409)
            graph = self._published_snapshot(project_id, path)
            return read_test_dataset_page(graph, node_id, snapshot_id, source_id, cursor, limit)

    @map_operation
    def source_scope(self, project_id: str, snapshot_id: str) -> Path:
        """
        Return the pinned checkout root without loading or copying any graph.

        Missing projects/snapshots use existing StoreError responses; a newer
        published revision raises 409 before allocating an analysis context.
        The caller rechecks the snapshot after acquiring its own resources.
        """
        with self.lock:
            project = self._project(project_id)
            snapshot_path = self._snapshot_path(project_id, '_graph')
            if snapshot_path.parent.name != snapshot_id:
                raise StoreError('Карта обновилась. Откройте контекст актуального снимка.', 409)
            return Path(project['path'])

    @map_operation
    def comparison_snapshot(self, project_id: str) -> tuple[Path, dict]:
        """Возвращает путь репозитория и один снимок для сравнения без группировки.

        Блокировка не позволяет публикации индекса разделить выбор метаданных
        и чтение карты. Git-команды выполняются уже после освобождения блокировки.
        """
        with self.lock:
            project_path = Path(self._project(project_id)["path"])
            snapshot_path = self._snapshot_path(project_id, "_graph")
            graph = self._remember_graph(project_id, snapshot_path)
            # Analysis receives the registry date while the cached immutable graph stays intact.
            metadata = {**graph.get("metadata", {}), "graphUpdatedAt": self._project(project_id).get("updatedAt")}
            return project_path, {**graph, "metadata": metadata}

    @map_operation
    def _read_snapshot_analysis(self, project_id: str, snapshot_id: str, reader) -> dict:
        """Run an audited read-only analysis against a pinned internally owned graph.

        The reader MUST NOT mutate the graph or expose it to mutable consumers.
        Internal analysis caches may retain only read-only references. The reader
        receives the source root and a shallow graph envelope, and must return
        bounded analysis facts.
        Published base/semantic graphs are replaced, never mutated, so the pinned
        reference survives cache eviction and concurrent publication. Only the
        returned facts are copied, outside the registry lock, to detach nested
        aliases before any route or response consumer can change them.

        A stale or replaced snapshot raises StoreError(409) before publication.
        Mutable/custom builders must keep using comparison_snapshot instead.
        """
        with self.lock:
            project_path = Path(self._project(project_id)["path"])
            snapshot_path = self._snapshot_path(project_id, "_graph")
            if snapshot_path.parent.name != snapshot_id:
                raise StoreError("Карта обновилась. Загрузите её перед анализом узла.", 409)
            graph = self._published_snapshot(project_id, snapshot_path)
            metadata = {**graph.get("metadata", {}),
                        "graphUpdatedAt": self._project(project_id).get("updatedAt")}
            pinned = {**graph, "metadata": metadata}
        result = deepcopy(reader(project_path, pinned))
        self.assert_snapshot(project_id, snapshot_id)
        return result

    @map_operation
    def health_snapshot(self, project_id: str, snapshot_id: str | None = None,
                        node_ids: list[str] | None = None, files: list[str] | None = None,
                        limit: int = 120, offset: int = 0, order: str | None = None) -> tuple[Path, dict]:
        """Копирует topology и выбранные health facts без дублирования тяжёлого полного графа.

        Сохранённая историческая ревизия читается отдельно; текущие исходники не
        проверяются и не перестраиваются этим запросом.
        """
        with self.lock:
            project = self._project(project_id)
            current = self._snapshot_path(project_id, "_graph")
            path = current
            if snapshot_id and snapshot_id != current.parent.name:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", snapshot_id):
                    raise StoreError("Invalid snapshot ID", 400)
                path = current.parent.parent / snapshot_id / current.name
                if not path.is_file() or path.is_symlink():
                    raise StoreError("Saved snapshot not found", 404)
            graph = self._published_snapshot(project_id, path) if path == current else self._read_graph(path)
            requested, selected_files = set(node_ids or []), set(files or [])
            all_ids = {node["id"] for node in graph["nodes"]}
            if requested - all_ids:
                raise StoreError("Health node not found in selected snapshot", 404)
            if requested:
                selected_files.update(node.get("file", "") for node in graph["nodes"] if node["id"] in requested)
            candidates = [node for node in graph["nodes"] if (node.get("complexity") or node.get("documentationHealth"))
                          and (not requested and not selected_files or node["id"] in requested or node.get("file") in selected_files)]
            if order:
                key = "documentationScore" if order == "documentation" else "score"
                candidates.sort(key=lambda n: ((n.get("health") or {}).get(key) is None,
                    (n.get("health") or {}).get(key) or 0, n.get("label", "")))
            selected = candidates[offset:offset + limit]
            selected_ids = {node["id"] for node in selected}
            keys = ("id", "label", "file", "line", "kind", "memberFacts", "healthIdentity", "healthSourceStamp", "healthParameterList", "_callable", "_callable_class")
            nodes = [{**{key: deepcopy(node[key]) for key in keys if key in node},
                      **({key: deepcopy(node[key]) for key in ("complexity", "documentationHealth", "health", "callableFacts", "healthCallFacts", "semanticFacts") if key in node} if node["id"] in selected_ids else {})}
                     for node in graph["nodes"]]
            edges = [{key: edge[key] for key in ("source", "target", "relation", "confidence", "line", "source_location", "evidence", "sourceHash", "callSite", "semanticEvidence", "runtimeSelectionKnown") if key in edge}
                     for edge in graph.get("edges", []) if edge.get("relation") in {"calls", "dispatches_to", "writes", "method", "inherits", "implements", "extends", "inherits_from"}]
            return Path(project["path"]), {"nodes": nodes, "edges": edges, "selected": deepcopy(selected),
                "metadata": {**deepcopy(graph.get("metadata", {})), "snapshotId": path.parent.name,
                             "graphUpdatedAt": project.get("updatedAt") if path == current else None},
                "totalMethods": len(candidates), "truncated": offset + limit < len(candidates),
                "scopeFiles": sorted(selected_files), "requestedNodeIds": sorted(requested)}

    @map_operation
    def federation_snapshots(self, primary_id: str, snapshot_id: str, related_ids: list[str]) -> list[dict]:
        """Закрепляет все выбранные карты одной атомарной операцией чтения."""
        if len(related_ids) > 8 or len(set(related_ids)) != len(related_ids) or primary_id in related_ids:
            raise StoreError("Выберите до восьми различных дополнительных репозиториев.", 400)
        with self.lock:
            snapshots = []
            for project_id in [primary_id, *sorted(related_ids)]:
                project = self._project(project_id)
                path = self._snapshot_path(project_id, "_graph")
                current_id = path.parent.name
                if project_id == primary_id and current_id != snapshot_id:
                    raise StoreError("Карта обновилась. Загрузите актуальный снимок.", 409)
                graph = self._remember_graph(project_id, path)
                snapshots.append({"id": project_id, "name": project["name"], "path": Path(project["path"]),
                                  "snapshotId": graph["metadata"]["snapshotId"], "graph": graph})
            return snapshots

    @map_operation
    def assert_snapshots(self, snapshots: dict[str, str]) -> None:
        """Проверяет все ревизии под одной блокировкой перед выдачей общей карты."""
        with self.lock:
            for project_id, snapshot_id in snapshots.items():
                if self._snapshot_path(project_id, "_graph").parent.name != snapshot_id:
                    raise StoreError("Один из репозиториев обновился. Загрузите общую карту заново.", 409)

    @map_operation
    def assert_snapshot(self, project_id: str, snapshot_id: str) -> None:
        """Проверяет актуальность ревизии под блокировкой без повторного чтения JSON."""
        with self.lock:
            snapshot_path = self._snapshot_path(project_id, "_graph")
            if snapshot_path.parent.name != snapshot_id:
                raise StoreError("Карта обновилась во время поиска входов. Загрузите её заново.", 409)

    @map_operation
    def source_snapshot(self, project_id: str, node_id: str, snapshot_id: str) -> tuple[Path, dict]:
        """Атомарно закрепляет проект и узел текущего снимка для просмотра исходника."""
        with self.lock:
            project_path = Path(self._project(project_id)["path"])
            snapshot_path = self._snapshot_path(project_id, "_graph")
            if snapshot_path.parent.name != snapshot_id:
                raise StoreError("Карта обновилась. Загрузите актуальный снимок перед просмотром кода.", 409)
            graph = self._published_snapshot(project_id, snapshot_path)
            for node in graph["nodes"]:
                if node["id"] == node_id:
                    return project_path, deepcopy(node)
            raise StoreError("Узел не найден в выбранном снимке карты.", 404)

    @map_operation
    def search_project_nodes(self, project_id: str, snapshot_id: str | None, query: str,
                             limit: int = 120, exclude_kinds: list[str] | None = None) -> dict:
        """Ищет в закреплённом снимке, копируя только выбранные карточки, без полной копии графа.

        Блокировка сохраняет одну ревизию на время поиска. Внутренний кэш остаётся
        собственностью хранилища; даже вложенные факты результата независимы от него.
        Если snapshot_id не задан и опубликованный снимок потерян, одна попытка
        восстановления запускается с SnapshotRecoveryPending (HTTP409/code/jobId).
        Закреплённый поиск не перестраивает историю; намеренно очищенная карта
        остаётся без автоматической сборки.
        """
        from .agent_views import search_nodes
        if snapshot_id is None:
            from .snapshot_recovery import ensure_current_snapshot
            ensure_current_snapshot(self, project_id)
        with self.lock:
            snapshot_path = self._snapshot_path(project_id, "_graph")
            if snapshot_id and snapshot_path.parent.name != snapshot_id:
                raise StoreError("Карта обновилась во время поиска входов. Загрузите её заново.", 409)
            graph = self._published_snapshot(project_id, snapshot_path)
            return deepcopy(search_nodes(graph, query, limit, exclude_kinds,
                                          load_fact_index=lambda: self._fact_index(project_id, graph)))

    def _fact_index(self, project_id: str, graph: dict):
        """Lazily retain at most one bounded fact index, keyed by the published source and overlay stamp.

        The caller holds the store lock. A broad name match never triggers index
        construction, and graphs outside the existing retention budget fall back.
        Return rows aligned with graph nodes, or None for exact uncached traversal.
        No graph/node references escape into the index or keep evicted graphs alive.
        """
        cached = self._published_graph.get(project_id)
        semantic_revision = graph.get('metadata', {}).get('semanticEnrichment', {}).get('revision')
        if cached is None or cached[2] is not graph and not semantic_revision:
            return None
        key = (project_id, cached[0], tuple(cached[1]), semantic_revision)
        if self._search_facts_key != key:
            from .fact_search_index import build_fact_index
            self._search_facts = None
            self._search_facts_key = key
            self._search_facts = build_fact_index(graph)
        return self._search_facts

    @map_operation
    def graph_context_snapshot(self, project_id: str, node_id: str, snapshot_id: str,
                               direction: str | None = None, limit: int = 40,
                               cursor: str | None = None) -> dict:
        """Выбирает ограниченные факты узла из одной ревизии без копии всей карты.

        Читатели копируют только возвращаемые факты и не меняют внутренний снимок.
        Блокировка закрепляет ревизию на время выборки; HTTP-слой дополнительно
        проверяет публикацию новой карты после получения результата.
        """
        with self.lock:
            snapshot_path = self._snapshot_path(project_id, "_graph")
            if snapshot_path.parent.name != snapshot_id:
                raise StoreError("Карта обновилась. Загрузите её перед чтением узла.", 409)
            graph = self._published_snapshot(project_id, snapshot_path)
            if direction is None:
                return read_graph_node(graph, node_id)
            if cursor is not None:
                return read_graph_neighbors(graph, node_id, direction, limit, cursor)
            return read_graph_neighbors(graph, node_id, direction, limit)

    @map_operation
    def source_node_snapshot(self, project_id: str, node_id: str, snapshot_id: str) -> tuple[Path, dict]:
        """
        Copy one source-owned node and scalar provenance without cloning its full graph.

        The source reader only needs captured byte hashes and snapshot dates.
        Return response-owned facts under the exact revision lock; unsupported or
        absent node IDs raise StoreError(404), and a changed snapshot raises 409.
        This does not replace full context for semantic description builders.
        """
        with self.lock:
            path = self._snapshot_path(project_id, '_graph')
            if path.parent.name != snapshot_id:
                raise StoreError('Карта обновилась. Загрузите её перед чтением исходника.', 409)
            graph = self._published_snapshot(project_id, path)
            selected = next((node for node in reversed(graph['nodes']) if node['id'] == node_id), None)
            if selected is None:
                raise StoreError('Узел не найден в текущей карте.', 404)
            enrichment = graph.get('metadata', {}).get('semanticEnrichment')
            revision = enrichment.get('revision') if isinstance(enrichment, dict) else None
            metadata = {'snapshotId': snapshot_id, 'semanticRevision': revision if isinstance(revision, str) else ''}
            for key in ('snapshotCreatedAt', 'graphUpdatedAt', 'createdAt'):
                value = graph.get('metadata', {}).get(key)
                if isinstance(value, str):
                    metadata[key] = value
            result = {'nodes': [deepcopy(selected)], 'edges': [], 'metadata': metadata}
            return Path(self._project(project_id)['path']), result

    @map_operation
    def mcp_snapshot(self, project_id: str, snapshot_id: str, node_id: str | None = None) -> Path:
        """Закрепляет общий граф MCP в ревизии клиента до запуска отдельного процесса.

        Публикация новой карты не может вклиниться между проверкой token, узла
        и выбором MCP-пути. Уже закреплённый файл старой ревизии не изменяется.
        """
        with self.lock:
            snapshot_path = self._snapshot_path(project_id, "_graph")
            if snapshot_path.parent.name != snapshot_id:
                raise StoreError("Карта обновилась. Загрузите её заново перед запросом MCP.", 409)
            if node_id is not None:
                return self.description_snapshot(project_id, node_id)
            return self._mcp_graph_path(project_id, snapshot_path)

    def _mcp_graph_path(self, project_id: str, snapshot_path: Path) -> Path:
        """Выбирает обогащённый файл этой ревизии; старые снимки используют raw.

        Явно отложенная проекция создаётся из канонического снимка по запросу.
        У старых ревизий missing-файл остаётся ошибкой без отката к raw;
        вызывается под общей блокировкой выбора снимка.
        """
        if not self._project(project_id).get("_mcpGraph"):
            canonical = self._published_snapshot(project_id, snapshot_path)
            if not canonical.get('metadata', {}).get('semanticEnrichment'):
                return self.raw_graph(project_id)
        else:
            canonical = self._published_snapshot(project_id, snapshot_path)
        revision = canonical.get('metadata', {}).get('semanticEnrichment', {}).get('revision')
        if revision:
            # New derivative names leave previously handed-out MCP snapshots immutable.
            expected = snapshot_path.with_name(f'mcp-semantic-{revision}.json')
            if not expected.exists():
                write_json(expected, build_mcp_graph(canonical))
                retired = sorted(snapshot_path.parent.glob('mcp-semantic-*.json'),
                                 key=lambda item: item.stat().st_mtime_ns, reverse=True)
                for item in retired[2:]:
                    item.unlink(missing_ok=True)
            return expected
        project = self._project(project_id)
        if project.get('_mcpGraphLazy'):
            expected = snapshot_path.with_name('mcp-graph.json')
            if Path(project['_mcpGraph']).resolve() != expected:
                raise StoreError('MCP-граф относится к другой ревизии. Перестройте проект.', 409)
            if not expected.exists():
                canonical = self._published_snapshot(project_id, snapshot_path)
                try:
                    write_json(expected, build_mcp_graph(canonical))
                except Exception as error:
                    raise StoreError('Не удалось создать производный MCP-граф. Карта сохранена; повторите запрос.', 503) from error
        path = self._snapshot_path(project_id, "_mcpGraph")
        if path.parent != snapshot_path.parent:
            raise StoreError("MCP-граф относится к другой ревизии. Перестройте проект.", 409)
        return path

    @map_operation
    def description_snapshot(self, project_id: str, node_id: str) -> Path:
        """Проверяет узел и выбирает MCP-файл одной ревизии под общей блокировкой."""
        with self.lock:
            snapshot_path = self._snapshot_path(project_id, "_graph")
            graph = self._published_snapshot(project_id, snapshot_path)
            if not any(node["id"] == node_id for node in graph["nodes"]):
                raise StoreError("Узел не найден в текущей карте", 404)
            return self._mcp_graph_path(project_id, snapshot_path)

    def close(self):
        """Завершает уже начатую сборку до закрытия хранилища."""
        self.executor.shutdown(wait=True, cancel_futures=True)
