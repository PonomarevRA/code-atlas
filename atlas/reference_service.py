"""Каталог, поиск, первичная индексация и явное обновление языковых справочников."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .reference_documents import LANGUAGES, ReferenceError
from .reference_store import ReferenceStore, import_archive, now
from .reference_vectors import ReferenceVectors
from .reference_localization import family, prefer_documents

MAX_SEARCH_EDITIONS = 32


class ReferenceService:
    """Один writer для первичных и ручных индексов; поиск не запускает обновления."""
    search_edition_limit = MAX_SEARCH_EDITIONS

    def __init__(self, data_dir: Path, embedder, bundled_dir: Path | None = None, bundled_vectors: Path | None = None):
        """Создаёт лёгкие handles и writer, без чтения документов или инициализации модели."""
        self.store = ReferenceStore(data_dir, bundled_dir)
        self.vectors = ReferenceVectors(Path(data_dir) / "reference-search", embedder, bundled_vectors)
        self.embedder = embedder
        self.lock = RLock()
        self.jobs = {}
        self.active = None
        self.initial_indexing = None
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="language-references")

    def catalog(self) -> dict:
        """Перечисляет версии и состояние индексов; отсутствующая модель не мешает чтению."""
        packs = []
        for pack in self.store.catalog():
            packs.append({**self.store.public(pack), "vectorIndex": self.vectors.status(pack["id"])})
        with self.lock:
            job = self._catalog_job(self.jobs.get(self.active))
            initial = self.initial_indexing
            if initial and initial.get("id") in self.jobs:
                initial = self.jobs[initial["id"]]
            initial = self._catalog_job(initial)
        return {"packs": packs, "languages": list(LANGUAGES), "offline": True,
                "modelAvailable": self.embedder.available(), "activeJob": job, "initialIndexing": initial}

    @staticmethod
    def _catalog_job(job: dict | None) -> dict | None:
        """Каталог/MCP получают компактные счётчики; подробные ошибки остаются в job API."""
        if job is None:
            return None
        result = deepcopy(job)
        if result["operation"] == "index-missing" and "result" in result:
            failures = result["result"].pop("failedPacks")
            result["result"]["failedPackCount"] = len(failures)
        return result

    def start_initial_indexing(self) -> dict:
        """Один раз за запуск ставит недостающие индексы в фон; готовые остаются доступны.

        Нет сетевой установки модели или повторного импорта документации. При следующем
        запуске продолжаются только корпуса без опубликованного векторного поколения.
        """
        with self.lock:
            if self.closed:
                raise ReferenceError("Atlas is shutting down", 503)
            if self.initial_indexing is None:
                if not self.embedder.available():
                    self.initial_indexing = {"operation": "index-missing", "status": "model-unavailable"}
                else:
                    self.initial_indexing = self.submit("index-missing")
            initial = self.initial_indexing
            return deepcopy(self.jobs.get(initial.get("id"), initial))

    def submit(self, operation: str, **arguments) -> dict:
        """Сериализует начальную подготовку и ручные операции без неограниченной очереди."""
        if operation == "index":
            arguments["pack"] = self.store.pack(arguments.pop("pack_id"))
            if not self.embedder.available():
                raise ReferenceError("Local embedding model is not installed", 503)
        elif operation not in {"import", "index-missing"}:
            raise ReferenceError("Unknown reference operation")
        with self.lock:
            if self.closed:
                raise ReferenceError("Atlas is shutting down", 503)
            if self.active:
                raise ReferenceError("A reference operation is already running", 409)
            job_id = uuid4().hex
            job = {"id": job_id, "operation": operation, "status": "running", "startedAt": now(), "progress": {}}
            self.jobs[job_id] = job
            self.active = job_id
            self.executor.submit(self._run, job_id, operation, arguments)
            return deepcopy(job)

    def _run(self, job_id, operation, arguments):
        """Публикует результат или ошибку; прежний корпус доступен весь процесс обновления."""
        def progress(**value):
            with self.lock:
                self.jobs[job_id]["progress"].update(value)
        try:
            if operation == "import":
                result = import_archive(arguments["archive"], arguments["metadata"], self.store.directory, progress)
            elif operation == "index-missing":
                result = self._index_missing(progress)
            else:
                result = self.vectors.build(arguments["pack"], progress, lambda: self.closed)
            with self.lock:
                self.jobs[job_id].update(status="completed", result=result)
                if operation == "index-missing" and result["failedPacks"]:
                    self.jobs[job_id].update(status="failed", error=f"{len(result['failedPacks'])} reference indexes failed")
        except Exception as error:
            with self.lock:
                self.jobs[job_id].update(status="failed", error=str(error)[:1000])
        finally:
            with self.lock:
                self.jobs[job_id]["finishedAt"] = now()
                if self.initial_indexing and self.initial_indexing.get("id") == job_id:
                    self.initial_indexing = deepcopy(self.jobs[job_id])
                self.active = None
                # Последний результат доступен после завершения; история ограничена.
                while len(self.jobs) > 32:
                    del self.jobs[next(iter(self.jobs))]

    def _index_missing(self, progress) -> dict:
        """Последовательно готовит корпуса; ошибка одного не мешает остальным.

        Каждый индекс публикуется отдельно: готовые результаты доступны до завершения
        всей подготовки. Существующие поколения, включая встроенные, не перестраиваются.
        """
        packs = self.store.catalog()
        indexed, skipped, failures = 0, 0, []
        progress(totalPacks=len(packs), processedPacks=0)
        for position, pack in enumerate(packs):
            if self.closed:
                raise ReferenceError("Initial indexing cancelled", 409)
            progress(packId=pack["id"], title=pack["title"], indexed=0, total=pack["documentCount"],
                     processedPacks=position)
            try:
                if self.vectors.status(pack["id"]) is not None:
                    skipped += 1
                else:
                    self.vectors.build(pack, progress, lambda: self.closed)
                    indexed += 1
            except Exception as error:
                if self.closed:
                    raise
                failures.append({"packId": pack["id"], "error": str(error)[:1000]})
            progress(processedPacks=position + 1, indexedPacks=indexed, skippedPacks=skipped,
                     failedPacks=len(failures))
        return {"indexedPacks": indexed, "skippedPacks": skipped, "failedPacks": failures}

    def job(self, job_id: str) -> dict:
        """Возвращает прогресс задачи текущей сессии; каталоги переживают перезапуск."""
        with self.lock:
            if job_id not in self.jobs:
                raise ReferenceError("Job not found; reload the reference catalog", 404)
            return deepcopy(self.jobs[job_id])

    def search(self, query: str, language: str = "", version: str = "", pack_id: str = "",
               locale: str = "en", mode: str = "hybrid", limit: int = 10) -> dict:
        """Фильтрует версии до поиска; точный API сильнее lexical/semantic кандидатов."""
        packs = self.store.catalog()
        if pack_id:
            selected = self.store.pack(pack_id)
            if language and selected["language"] != language or version and selected["version"] != version:
                raise ReferenceError("Pack does not match requested language/version")
            # Явно выбранный русский корпус при English остаётся русским; выбор
            # английского корпуса допускает его русский перевод и обратный fallback.
            packs = [p for p in packs if family(p) == family(selected)
                     and p["locale"] in {locale, "en", selected["locale"]}]
            if selected["locale"] not in {"en", locale}:
                packs = [selected]
        else:
            packs = [p for p in packs if (not language or p["language"] == language) and (not version or p["version"] == version)]
        editions = {}
        for pack in packs:
            key = family(pack)
            if not pack_id and pack["locale"] not in {locale, "en"}:
                continue
            versions = editions.setdefault(key, {})
            versions.setdefault(pack["locale"], pack)
        groups = list(editions.values())
        warnings = []
        if len(groups) > self.search_edition_limit:
            warnings.append(f"Search limited to {self.search_edition_limit} editions; select a pack or version")
        packs = [p for group in groups[:self.search_edition_limit] for p in group.values()]
        results = []
        query_vector = None
        semantic = mode in {"hybrid", "semantic"}
        for pack in packs:
            # Один запасной кандидат позволяет честно отметить скрытые overloads/results.
            hits = self.store.search_pack(pack, query, "exact" if mode == "exact" else "fulltext", limit + 1)
            semantic_ranks = {}
            if semantic and not any(h["match"] == "exact" for h in hits):
                index = self.vectors.status(pack["id"])
                if index and self.embedder.available():
                    if query_vector is None:
                        # Бюджет запросов тот же, что у локальной модели проектов.
                        parts = self.embedder.split_text(query)
                        if len(parts) != 1:
                            raise ReferenceError("Semantic query exceeds model token budget")
                        query_vector = self.embedder.query(query)
                    try:
                        ids = self.vectors.search(pack, query_vector, limit + 1)
                    except ReferenceError as error:
                        warnings.append(f"{pack['id']}: {error}")
                        ids = []
                    semantic_ranks = {doc_id: rank for rank, doc_id in enumerate(ids, 1)}
                    for doc_id in ids:
                        if any(h["documentId"] == doc_id for h in hits):
                            continue
                        doc = self.store.document(pack["id"], doc_id, limit=1000)
                        hits.append({"documentId": doc_id, "packId": pack["id"], "title": doc["title"],
                                     "symbol": doc["symbol"], "signature": doc["signature"], "excerpt": doc["summary"] or doc["text"],
                                     "excerptScope": "author-summary" if doc["summary"] else "document-head",
                                     "excerptTruncated": doc["summaryTruncated"] if doc["summary"] else doc["nextOffset"] is not None, "match": "semantic",
                                     "version": pack["version"], "edition": pack["edition"], "locale": pack["locale"],
                                     "sourceUrl": doc["sourceUrl"], "sourcePath": doc["path"], "score": 0})
                else:
                    warnings.append(f"{pack['id']}: semantic index unavailable; exact/fulltext search remains available")
            # Reciprocal rank внутри каждого метода без сравнения cosine с BM25.
            ranks = {"fulltext": 0, "semantic": 0}
            for hit in hits:
                hit["language"] = pack["language"]
                ranks[hit["match"]] = ranks.get(hit["match"], 0) + 1
                rank = semantic_ranks[hit["documentId"]] if hit["match"] == "semantic" else ranks[hit["match"]]
                hit["rankScore"] = 1 if hit["match"] == "exact" else 1 / (60 + rank)
                if hit["match"] == "fulltext" and hit["documentId"] in semantic_ranks:
                    hit["rankScore"] += 1 / (60 + semantic_ranks[hit["documentId"]])
                    hit["match"] = "hybrid"
            results.extend(hits)
        results = prefer_documents(self.store, results, packs, locale)
        results.sort(key=lambda h: (-h["rankScore"], h["locale"] != locale, h["packId"], h["documentId"]))
        return {"results": results[:limit], "truncated": len(results) > limit or len(editions) > self.search_edition_limit, "warnings": warnings,
                "requestedLocale": locale, "searchedPacks": [self.store.public(p) for p in packs],
                "offline": True, "evidence": "Documentation candidates; API binding must be verified against source types and imports."}

    def request_stop(self):
        """Cancel indexing at its next checkpoint without joining the writer or closing search handles."""
        self.closed = True

    def close(self):
        """Останавливает writer; незавершённый векторный индекс не публикуется."""
        with self.lock:
            self.request_stop()
        self.executor.shutdown(wait=True)
