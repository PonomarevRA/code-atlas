"""Фоновый индекс документов: снимок карты, рабочие комментарии и компактный поиск."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .store import StoreError


class SemanticService:
    """Один writer строит производные индексы; запросы никогда не читают репозиторий.

    Модель загружается только из заранее подготовленного локального кэша.
    Неудачная индексация не меняет опубликованный граф или прежний индекс.
    """

    def __init__(self, store, database=None, embedder=None):
        from .semantic_embeddings import LocalEmbedding
        from .semantic_index import IndexDatabase

        self.store = store
        cache = Path(os.environ.get("ATLAS_EMBEDDING_CACHE", str(store.data_dir / "models")))
        self.embedder = embedder or LocalEmbedding(cache)
        self.database = database or IndexDatabase(store.data_dir, self.embedder)
        self.lock = RLock()
        self.jobs = {}
        self.active_job = None
        self.deferred = {}
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="documentation-index")

    def status(self, project_id: str, snapshot_id: str) -> dict:
        """Возвращает состояние нужной ревизии без чтения исходников и загрузки ONNX."""
        self.store.assert_snapshot(project_id, snapshot_id)
        index = self.database.status(project_id, snapshot_id)
        with self.lock:
            active = self.jobs.get(self.active_job)
            job = deepcopy(active) if active and (active["projectId"], active["snapshotId"]) == (project_id, snapshot_id) else None
            deferred = self.deferred.get(project_id) == snapshot_id
        available = self.embedder.available()
        result = {"projectId": project_id, "snapshotId": snapshot_id, "index": index,
                  "status": "ready" if index else "not-indexed", "model": self.embedder.metadata(),
                  "modelAvailable": available, "job": job}
        result["deferred"] = deferred
        if not available:
            result["status"] = "model-unavailable"
            result["message"] = "Подготовьте локальную модель командой из docs/semantic-search.md."
        if job and job["snapshotId"] == snapshot_id:
            result["status"] = job["status"]
        elif deferred:
            result.update(status="deferred", message="Индекс ожидает освобождения writer.")
        self.store.assert_snapshot(project_id, snapshot_id)
        return result

    def submit(self, project_id: str, snapshot_id: str, force: bool = False) -> dict:
        """Закрепляет карту, ставит индекс в фон и повторно использует активную задачу."""
        root, graph = self.store.comparison_snapshot(project_id)
        self.store.assert_snapshot(project_id, snapshot_id)
        if graph["metadata"]["snapshotId"] != snapshot_id:
            raise StoreError("Карта обновилась. Выберите актуальный снимок.", 409)
        if not self.embedder.available():
            raise StoreError("Локальная модель не подготовлена. См. docs/semantic-search.md.", 503)
        with self.lock:
            if self.closed:
                raise StoreError("Приложение завершает работу", 503)
            if self.active_job:
                active = self.jobs[self.active_job]
                if (active["projectId"], active["snapshotId"]) == (project_id, snapshot_id):
                    return {"job": deepcopy(active)}
                raise StoreError("Дождитесь текущей индексации документов", 409)
            job = {"id": uuid4().hex, "projectId": project_id, "snapshotId": snapshot_id,
                   "status": "queued", "phase": "capture"}
            self.jobs[job["id"]] = job
            if self.deferred.get(project_id) == snapshot_id:
                self.deferred.pop(project_id)
            self.active_job = job["id"]
            self.executor.submit(self._build, job["id"], root, graph, force)
            return {"job": deepcopy(job)}

    def snapshot_published(self, project_id: str, snapshot_id: str):
        """При занятом writer запоминает последнюю ревизию проекта вместо потери события."""
        if self.embedder.available():
            try:
                self.submit(project_id, snapshot_id)
            except StoreError:
                with self.lock:
                    if self.active_job and not self.closed:
                        try:
                            self.store.assert_snapshot(project_id, snapshot_id)
                        except StoreError:
                            # Запоздавшее событие не заменяет уже ожидающий новый snapshot.
                            return
                        self.deferred[project_id] = snapshot_id

    def _run_deferred(self):
        """После worker запускает следующую актуальную ревизию; stale очередь пропускается."""
        while True:
            with self.lock:
                if self.closed or self.active_job or not self.deferred:
                    return
                project_id = next(iter(self.deferred))
                snapshot_id = self.deferred.pop(project_id)
            try:
                self.submit(project_id, snapshot_id)
                return
            except StoreError:
                with self.lock:
                    if self.active_job and not self.closed:
                        # HTTP мог занять writer между pop и submit. Новое событие имеет приоритет.
                        self.deferred.setdefault(project_id, snapshot_id)
                        return
                # Snapshot мог смениться до своей очереди. Новое событие хранит новую ревизию.
                continue

    def _build(self, job_id: str, root: Path, graph: dict, force: bool):
        """Снимает документы один раз и публикует таблицу после проверки ревизии."""
        from .document_corpus import capture_documentation

        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "indexing"
            project_id, snapshot_id = job["projectId"], job["snapshotId"]
        try:
            corpus = capture_documentation(root, graph, snapshot_id)
            self.store.assert_snapshot(project_id, snapshot_id)
            with self.lock:
                job.update(phase="vectors", capturedChunks=len(corpus["chunks"]))
            index = self.database.build(project_id, snapshot_id, corpus,
                                        lambda: self.store.assert_snapshot(project_id, snapshot_id), force=force)
            with self.lock:
                job.update(status="completed", phase="ready", index=index)
        except Exception as error:
            with self.lock:
                job.update(status="failed", error=str(error)[-1000:] or type(error).__name__)
        finally:
            with self.lock:
                self.active_job = None
                while len(self.jobs) > 40:
                    self.jobs.pop(next(iter(self.jobs)))
            self._run_deferred()

    def get_job(self, job_id: str) -> dict:
        """Возвращает ограниченный прогресс индексации текущей сессии."""
        with self.lock:
            if job_id not in self.jobs:
                raise StoreError("Индексация не найдена. Проверьте состояние проекта.", 404)
            return deepcopy(self.jobs[job_id])

    def search(self, project_id: str, snapshot_id: str, query: str, limit: int, kind: str) -> dict:
        """Ищет локально и добавляет только существующие узлы с прямыми доказательствами."""
        query = query.strip()
        if not query:
            raise StoreError("Введите поисковый запрос", 400)
        _, graph = self.store.comparison_snapshot(project_id)
        self.store.assert_snapshot(project_id, snapshot_id)
        if graph["metadata"]["snapshotId"] != snapshot_id:
            raise StoreError("Карта обновилась. Выберите актуальный снимок.", 409)
        try:
            result = self.database.search(project_id, snapshot_id, query, limit=limit, kind=kind)
        except RuntimeError as error:
            raise StoreError(str(error), getattr(error, "status", 503)) from error
        nodes = {node["id"]: node for node in graph["nodes"]}
        intersections = {}
        for hit in result["hits"]:
            hit["text"] = hit["text"][:650]
            hit["nodeIds"] = [node_id for node_id in hit.get("nodeIds", []) if node_id in nodes][:12]
            for node_id in hit["nodeIds"]:
                if len(intersections) >= 30:
                    break
                node = nodes[node_id]
                intersections[node_id] = {key: node.get(key) for key in ("id", "label", "file", "line", "kind")}
        result.update(projectId=project_id, snapshotId=snapshot_id, query=query,
                      codeNodes=list(intersections.values()),
                      explanation="Косинусное расстояние: меньше — ближе по смыслу. Это не вероятность и не доказательство вызова.")
        self.store.assert_snapshot(project_id, snapshot_id)
        return result

    def close(self):
        """Дожидается writer перед закрытием соединения с LanceDB."""
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.database.close()
