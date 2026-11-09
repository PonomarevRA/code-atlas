"""Самостоятельный каталог карт; импорт запускается только create/refresh запросом."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
from threading import Event, RLock
from uuid import uuid4

from .map_storage import checked_path, identifier
from .map_maintenance import MapMaintenance, guard_executor, map_operation
from .data_models import ANALYSIS_VERSION, DataError, DataInput, DataQuery, require
from .data_sources import capture, digest, fingerprint, source_path, storage_bytes
from .data_storage import RecordWriter, encode, identity
from .data_relations import build_relations
from .data_queries import get_scenario, page, query_records, search_scenarios, read_database, record


def now():
    """UTC timestamp остаётся однозначным при показе в локальной зоне браузера."""
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value, *, exclusive=False):
    """Публикует файл через rename, не повреждая предыдущую ревизию при ошибке."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    owned = False
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            owned = True
            handle.write(encode(value))
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if owned:
            temporary.unlink(missing_ok=True)


class ImportCancellation:
    """Combine one job's cancellation with the nonblocking application stop signal."""

    def __init__(self, stop_requested):
        """Keep the shared stop event separate from this job's own cancellation."""
        self.local = Event()
        self.stop_requested = stop_requested

    def set(self):
        """Cancel this job without cancelling other jobs."""
        self.local.set()

    def is_set(self):
        """Workers cooperate with either their local or application cancellation."""
        return self.local.is_set() or self.stop_requested.is_set()


class DataMapStore:
    """Раздельные снимки/БД и ленивый единственный worker без фонового watcher."""

    def __init__(self, data_dir, *, maintenance=None):
        """
        Load only the data-map catalog without source discovery or import.

        Args:
            data_dir: Explicit application data root; canonical and legacy map layouts remain read-compatible.
            maintenance: Optional application-shared admission boundary; standalone stores create their own gate.
        """
        self.maintenance = maintenance or MapMaintenance()
        self.data_dir = Path(data_dir).resolve()
        self.root = self.data_dir / "maps" / "data-maps"
        self.registry = self.root / "catalog.json"
        self.lock, self.executor = RLock(), None
        registry = self._path("maps", "data-maps", "catalog.json", directory=False)
        if not registry.exists():
            registry = self._path("data-maps", "catalog.json", directory=False)
        self.catalog_published = registry == self.registry and registry.exists()
        self.maps = json.loads(registry.read_text()) if registry.exists() else {}
        require(isinstance(self.maps, dict) and all(isinstance(item, dict) for item in self.maps.values()),
                "INVALID_STORAGE", "Каталог карт повреждён.", 409)
        self.jobs, self.cancel_events = {}, {}
        self.stop_requested = Event()
        for item in self.maps.values():
            if item.get("status") in {"queued", "running"}:
                item.update(status="failed", error="Импорт был прерван. Обновите карту явно.")

    def _map(self, map_id):
        """ID используется только как ключ проверенного каталога."""
        require(map_id in self.maps, "MAP_MISSING", "Карта данных не найдена.", 404)
        return self.maps[map_id]

    def _path(self, *parts, directory=True):
        """Convert managed path violations to the public storage error contract."""
        try:
            return checked_path(self.data_dir, *parts, directory=directory)
        except ValueError:
            raise DataError("INVALID_STORAGE", "Небезопасный путь или конфликт объектов хранилища карты.", 409) from None

    def _identifier(self, value, *, snapshot=False):
        """Catalog IDs are validated before they can become filesystem components."""
        try:
            return identifier(value, snapshot=snapshot)
        except ValueError:
            raise DataError("INVALID_STORAGE", "Некорректный идентификатор хранилища карты.", 409) from None

    def directory(self, map_id=None):
        """Return the canonical write directory; legacy snapshots are resolved separately."""
        if map_id is None:
            return self._path("maps", "data-maps")
        self._map(map_id)
        return self._path("maps", "data-maps", self._identifier(map_id))

    def existing_directory(self, map_id):
        """Choose the folder actually present for opening, while writes remain canonical."""
        canonical = self.directory(map_id)
        if canonical.is_dir():
            return canonical
        return self._path("data-maps", self._identifier(map_id))

    def _save_catalog(self, catalog):
        """The first explicit mutation publishes the complete loaded catalog atomically."""
        path = self._path("maps", "data-maps", "catalog.json", directory=False)
        try:
            atomic_json(path, catalog, exclusive=not self.catalog_published)
        except FileExistsError:
            raise DataError("INVALID_STORAGE", "Каталог появился после запуска. Перезапустите Atlas для чтения новой ревизии.", 409) from None
        self.catalog_published = True

    def _snapshot_directory(self, item):
        """Pin one existing snapshot directory, preferring canonical without corrupt fallback."""
        map_id = self._identifier(item["id"])
        snapshot = item.get("snapshotId")
        require(snapshot is not None, "NOT_READY", "Карта ещё не построена. Запросите построение и дождитесь задания.", 409)
        snapshot = self._identifier(snapshot, snapshot=True)
        canonical = self._path("maps", "data-maps", map_id, snapshot)
        if canonical.exists():
            return canonical
        legacy = self._path("data-maps", map_id, snapshot)
        require(legacy.is_dir(), "SNAPSHOT_MISSING", "Файлы снимка отсутствуют. Обновите карту явно.", 409)
        return legacy

    def _snapshot_file(self, item, filename):
        """Validate the selected snapshot's leaf before opening metadata or SQLite."""
        directory = self._snapshot_directory(item)
        return self._path(*directory.relative_to(self.data_dir).parts, filename, directory=False)

    def _metadata(self, item):
        """Check metadata and database availability without repairing or publishing anything."""
        path = self._snapshot_file(item, "metadata.json")
        require(path.is_file(), "SNAPSHOT_MISSING", "Метаданные снимка отсутствуют. Обновите карту явно.", 409)
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            self._validate_metadata(metadata, item)
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            raise DataError("SNAPSHOT_CORRUPT", "Метаданные снимка повреждены. Обновите карту явно.", 409) from None
        database = self._snapshot_file(item, "records.sqlite")
        require(database.is_file(), "SNAPSHOT_MISSING", "База снимка отсутствует. Обновите карту явно.", 409)
        connection = None
        try:
            connection = read_database(database)
            for table, columns in {"records": "id,collection,ordinal,payload,provenance,parent",
                                   "cells": "record,collection,field,type,value,raw",
                                   "scenarios": "id,payload,name,tags"}.items():
                connection.execute(f"SELECT {columns} FROM {table} LIMIT 0")
        except sqlite3.DatabaseError:
            raise DataError("SNAPSHOT_CORRUPT", "База снимка повреждена. Обновите карту явно.", 409) from None
        finally:
            if connection is not None:
                connection.close()
        return metadata

    @staticmethod
    def _validate_metadata(metadata, item):
        """Reject malformed required structure before schema/export/query code can dereference it."""
        if not isinstance(metadata, dict) or metadata["mapId"] != item["id"] or metadata["snapshotId"] != item["snapshotId"]:
            raise ValueError("Snapshot identity mismatch")
        for field in ("records", "inputBytes"):
            if type(metadata[field]) is not int or metadata[field] < 0:
                raise ValueError("Invalid snapshot counter")
        for field in ("analysisVersion", "builtAt"):
            if not isinstance(metadata[field], str) or not metadata[field]:
                raise ValueError("Missing snapshot version/date")
        if not isinstance(metadata["files"], list) or not isinstance(metadata["collections"], dict):
            raise ValueError("Invalid snapshot schema")
        file_names = set()
        for file in metadata["files"]:
            for field in ("path", "sha256"):
                if not isinstance(file[field], str):
                    raise ValueError("Invalid source metadata")
            if (type(file["sizeBytes"]) is not int or file["sizeBytes"] < 0 or not isinstance(file["stamp"], list)
                    or len(file["stamp"]) != 4 or any(type(value) is not int for value in file["stamp"])):
                raise ValueError("Invalid source fingerprint")
            file_names.add(file["path"])
        for key, collection in metadata["collections"].items():
            if collection["id"] != key or collection["file"] not in file_names or not isinstance(collection["label"], str):
                raise ValueError("Invalid collection identity")
            if not isinstance(collection["kind"], str) or type(collection["count"]) is not int:
                raise ValueError("Invalid collection kind/count")
            if not isinstance(collection["fields"], list):
                raise ValueError("Invalid collection fields")
            for field in collection["fields"]:
                if (not isinstance(field, dict) or not isinstance(field["name"], str)
                        or type(field["indexed"]) is not bool):
                    raise ValueError("Invalid field")
        if not isinstance(metadata["gherkinNodes"], list):
            raise ValueError("Invalid Gherkin nodes")
        for node in metadata["gherkinNodes"]:
            if not isinstance(node["id"], str) or node["source"]["file"] not in file_names:
                raise ValueError("Invalid Gherkin source")
        relations = metadata["relations"]
        if not isinstance(relations["items"], list) or type(relations["truncated"]) is not bool:
            raise ValueError("Invalid relations")
        for rule in relations["items"]:
            for field in ("id", "sourceCollection", "targetCollection", "sourceField", "targetField", "semanticStatus", "status", "compareAs"):
                if not isinstance(rule[field], str):
                    raise ValueError("Invalid relation")
            if not isinstance(rule["coverage"], dict):
                raise ValueError("Invalid relation coverage")

    def freshness(self, item, full_hash=False):
        """Проверяет свежесть без построения; явный ensure также сравнивает SHA-256."""
        if not item.get("snapshotId"):
            return {"status": "not-built", "changedFiles": [], "checkedAt": now()}
        try:
            metadata = self._metadata(item)
        except DataError as error:
            if error.code not in {"SNAPSHOT_MISSING", "SNAPSHOT_CORRUPT", "INVALID_STORAGE"}:
                raise
            return {"status": "unavailable", "code": error.code, "message": str(error), "checkedAt": now()}
        changed = []
        for file in metadata["files"]:
            try:
                path = source_path(Path(item["root"]), file["path"])
                changed_before = fingerprint(path) != file["stamp"]
                if changed_before or (full_hash and (digest(path) != file["sha256"]
                                                     or fingerprint(path) != file["stamp"])):
                    changed.append(file["path"])
            except (OSError, DataError):
                changed.append(file["path"])
        shown, size = [], 0
        for filename in changed[:20]:
            size += len(filename.encode())
            if size > 2000:
                break
            shown.append(filename)
        return {"status": "stale" if changed else "current", "changedFiles": shown, "changedFileCount": len(changed),
                "truncated": len(changed) > len(shown),
                "checkedAt": now(), "verification": "sha256" if full_hash else "size-mtime-ctime-inode"}

    def public(self, item):
        """Список содержит дату снимка, текущую проверку свежести и отдельный URL слоя."""
        value = {key: item.get(key) for key in ("id", "title", "root", "projectId", "files", "status", "snapshotId", "builtAt", "lastVerifiedAt", "error", "jobId")}
        value["freshness"] = self.freshness(item)
        if value["freshness"]["status"] == "unavailable" and value["status"] == "ready":
            value["status"] = "unavailable"
        value["url"] = "/data.html?map=" + item["id"]
        return value

    def list(self, project_id=None):
        """Перечисляет существующие карты; ничего не сканирует и не строит."""
        with self.lock:
            return {"maps": [self.public(item) for item in self.maps.values() if project_id is None or item.get("projectId") == project_id]}

    @map_operation
    def create(self, options: DataInput, root: Path):
        """Регистрирует явный набор файлов и запрашивает его первую актуализацию."""
        root = root.resolve()
        require(root.is_dir() and not root.is_relative_to(self.data_dir), "INVALID_ROOT", "Выберите существующий каталог вне хранилища Atlas.")
        files = sorted(set(options.files))
        require(len(files) == len(options.files), "DUPLICATE_FILES", "Файлы в запросе повторяются.")
        for filename in files:
            require(not source_path(root, filename).is_relative_to(self.data_dir), "INVALID_PATH", "Хранилище Atlas нельзя включать во вход карты.")
        configuration = options.model_dump(exclude={"root", "title", "projectId"})
        configuration["files"] = files
        map_id = identity(str(root), configuration, options.projectId)
        with self.lock:
            require(not self.stop_requested.is_set(), "SERVICE_STOPPING", "Atlas завершает работу. Повторите запрос после запуска.", 503)
            if map_id not in self.maps:
                self.maps[map_id] = {"id": map_id, "title": options.title, "root": str(root), "projectId": options.projectId,
                                     "files": files, "options": configuration, "status": "not-built", "snapshotId": None}
        # refresh хеширует источники вне общей блокировки, включая первый create.
        return self.refresh(map_id)

    def _active_map_job(self, map_id):
        """Находит уже принятое задание карты; вызывающий держит блокировку каталога."""
        return next((job for job in self.jobs.values()
                     if job["mapId"] == map_id and job["status"] in {"queued", "running"}), None)

    @map_operation
    def refresh(self, map_id, force=False):
        """Только этот явный запрос запускает worker; актуальная карта не перестраивается."""
        while True:
            with self.lock:
                require(not self.stop_requested.is_set(), "SERVICE_STOPPING", "Atlas завершает работу. Повторите запрос после запуска.", 503)
                item = self._map(map_id)
                active = self._active_map_job(map_id)
                if active:
                    return {"map": self.public(item), "job": deepcopy(active)}
                checked_item = deepcopy(item)
            # Чтение до 1 ГБ не задерживает списки, запросы и отмену других заданий.
            fresh = self.freshness(checked_item, full_hash=True)
            with self.lock:
                require(not self.stop_requested.is_set(), "SERVICE_STOPPING", "Atlas завершает работу. Повторите запрос после запуска.", 503)
                item = self._map(map_id)
                active = self._active_map_job(map_id)
                if active:
                    return {"map": self.public(item), "job": deepcopy(active)}
                if item.get("snapshotId") != checked_item.get("snapshotId"):
                    # Другой запрос успел опубликовать карту; проверяем её новые источники.
                    continue
                if fresh["status"] == "current" and not force:
                    current = self.freshness(item)
                    if current["status"] == "current":
                        item["lastVerifiedAt"] = fresh["checkedAt"]
                        self._save_catalog(self.maps)
                        return {"map": self.public(item), "job": None, "verified": True}
                job_id = uuid4().hex
                job = {"id": job_id, "mapId": map_id, "status": "queued", "phase": "queued", "bytesRead": 0, "records": 0, "requestedAt": now()}
                item.update(status="queued", jobId=job_id, error=None)
                self.jobs[job_id], self.cancel_events[job_id] = job, ImportCancellation(self.stop_requested)
                self._save_catalog(self.maps)
                if self.executor is None:
                    self.executor = guard_executor(ThreadPoolExecutor(max_workers=1, thread_name_prefix="atlas-data"), self.maintenance)
                self.executor.submit(self._build, job_id)
                return {"map": self.public(item), "job": deepcopy(job)}

    def job(self, job_id, cancel=False):
        """Отмена является отдельным явным действием и сохраняет предыдущий снимок."""
        with self.lock:
            require(job_id in self.jobs, "JOB_MISSING", "Задание данных не найдено.", 404)
            if cancel:
                self.cancel_events[job_id].set()
            return deepcopy(self.jobs[job_id])

    def _build(self, job_id):
        """Собирает staging и атомарно переключает каталог лишь после всех проверок."""
        with self.lock:
            job = self.jobs[job_id]
            item = deepcopy(self._map(job["mapId"]))
            job["status"] = self.maps[item["id"]]["status"] = "running"
        event = self.cancel_events[job_id]
        staging = None
        staging_owned = False
        writer = None

        def progress(**values):
            """Обновляет только небольшой объект задания, не каталог после каждой строки."""
            with self.lock:
                job.update(values)
        try:
            from .data_csv import import_csv
            from .data_json import import_json
            from .data_gherkin import import_gherkin
            staging = self._path("maps", "data-maps", self._identifier(item["id"]), ".staging-" + job_id)
            staging.mkdir(parents=True)
            staging_owned = True
            files = capture(Path(item["root"]), item["files"], staging / "sources", progress, event.is_set)
            writer = RecordWriter(staging / "records.sqlite", item["id"], event.is_set, progress, item["options"]["indexFields"])
            writer.connection.set_progress_handler(lambda: int(event.is_set()), 1000)
            gherkin, gherkin_bytes = [], 0
            for file in files:
                path = staging / "sources" / file["captured"]
                progress(file=file["path"], phase="parse")
                if path.suffix == ".csv":
                    import_csv(path, file, writer, item["options"])
                elif path.suffix == ".feature":
                    nodes = import_gherkin(path, file, writer)
                    gherkin_bytes += len(encode(nodes).encode())
                    require(gherkin_bytes <= 16000000, "SCHEMA_LIMIT", "Метаданные Gherkin превышают 16 МБ; разделите карту.")
                    gherkin.extend(nodes)
                    require(len(gherkin) <= 20000, "SCHEMA_LIMIT", "Gherkin AST всей карты превышает 20000 узлов.")
                else:
                    import_json(path, file, writer)
            progress(records=writer.count, phase="relations")
            relations = build_relations(writer.connection, writer.collections, item["options"]["relations"], event.is_set)
            metadata = {"mapId": item["id"], "snapshotId": job_id, "analysisVersion": ANALYSIS_VERSION,
                        "files": files, "collections": writer.collections, "gherkinNodes": gherkin, "relations": relations,
                        "records": writer.count, "builtAt": now(), "inputBytes": sum(file["sizeBytes"] for file in files)}
            writer.close()
            writer = None
            progress(phase="verify")
            for file in files:
                require(fingerprint(source_path(Path(item["root"]), file["path"])) == file["stamp"],
                        "SOURCE_CHANGED", "Исходники изменились во время анализа; запросите обновление.", 409)
            require(not event.is_set(), "CANCELLED", "Импорт отменён.", 409)
            atomic_json(staging / "metadata.json", metadata)
            destination = self._path("maps", "data-maps", item["id"], job_id)
            require(not destination.exists(), "INVALID_STORAGE", "Путь нового снимка уже занят.", 409)
            staging.rename(destination)
            with self.lock:
                current = self.maps[item["id"]]
                published = {**current, "snapshotId": job_id, "status": "ready", "builtAt": metadata["builtAt"], "lastVerifiedAt": now(), "error": None}
                catalog = {**self.maps, item["id"]: published}
                self._save_catalog(catalog)
                self.maps = catalog
                job.update(status="completed", phase="completed", completedAt=now(), snapshotId=job_id)
        except Exception as error:
            code = error.code if isinstance(error, DataError) else "IMPORT_FAILED"
            message = str(error)[:500] if isinstance(error, DataError) else "Не удалось разобрать данные. Проверьте формат, кодировку, свободное место и лимиты."
            with self.lock:
                job.update(status="cancelled" if event.is_set() else "failed", error=message, code=code)
                self.maps[item["id"]].update(status=job["status"], error=message)
                self._save_catalog(self.maps)
        finally:
            if writer:
                writer.close(commit=False)
            if staging_owned and staging.exists():
                shutil.rmtree(staging)

    @map_operation
    def snapshot(self, map_id, snapshot_id=None):
        """Чтение закрепляет снимок; устаревание источников возвращается как метаданные."""
        with self.lock:
            item = self._map(map_id)
            require(item.get("snapshotId"), "NOT_READY", "Карта ещё не построена.", 409)
            require(snapshot_id is None or snapshot_id == item["snapshotId"], "SNAPSHOT_MISMATCH", "Снимок изменился. Получите обзор актуальной карты.", 409)
            return deepcopy(item), self._metadata(item)

    @map_operation
    def overview(self, map_id):
        """Показывает дату и изменения источников, не принимая решение за пользователя."""
        with self.lock:
            item = self._map(map_id)
            result = self.public(item)
            if item.get("snapshotId") and result["freshness"]["status"] != "unavailable":
                metadata = self._metadata(item)
                result.update(inputBytes=metadata["inputBytes"], records=metadata["records"], collectionCount=len(metadata["collections"]), analysisVersion=metadata["analysisVersion"])
            return result

    @map_operation
    def query(self, map_id, operation, query: DataQuery):
        """Закрепляет снимок и сообщает дату/свежесть; чтение никогда не строит карту."""
        item, metadata = self.snapshot(map_id, query.snapshotId)
        from .data_graph import schema_nodes, trace_links
        from .data_keys import rank_keys, key_values, key_path
        path = self._snapshot_file(item, "records.sqlite")
        connection = None
        try:
            connection = read_database(path)
            if operation == "schema":
                result = page(schema_nodes(metadata, query.nodeId), map_id, query, operation)
            elif operation == "relations":
                result = page(metadata["relations"]["items"], map_id, query, operation)
                result["coverage"] = {key: value for key, value in metadata["relations"].items() if key != "items"}
            elif operation == "records":
                result = query_records(connection, metadata, map_id, query)
            elif operation == "record":
                row = connection.execute("SELECT * FROM records WHERE id=?", (query.recordId,)).fetchone()
                require(row is not None, "RECORD_MISSING", "Запись не найдена в этой карте.", 404)
                result = page([record(row, query.fields)], map_id, query, operation)
            elif operation == "scenarios":
                result = search_scenarios(connection, map_id, query)
            elif operation == "scenario":
                result = get_scenario(connection, map_id, query)
            elif operation in {"keys", "key-values", "key-path"}:
                handler = {"keys": rank_keys, "key-values": key_values, "key-path": key_path}[operation]
                result = handler(connection, metadata, map_id, query)
            elif operation in {"trace", "view"}:
                result = trace_links(connection, metadata, map_id, query)
                if operation == "view":
                    result.update(id=uuid4().hex, title=query.title, createdAt=now())
                    result["url"] = f"/data.html?map={map_id}&saved={result['id']}"
            else:
                raise DataError("INVALID_OPERATION", "Неизвестный запрос данных.")
            self.snapshot(map_id, query.snapshotId)
            result["analysisVersion"] = ANALYSIS_VERSION
            result["builtAt"] = metadata["builtAt"]
            result["lastVerifiedAt"] = item.get("lastVerifiedAt")
            result["freshness"] = self.freshness(item)
            if operation == "view":
                legacy = self._path("data-maps", map_id, "views", result["id"] + ".json", directory=False)
                require(not legacy.exists(), "INVALID_STORAGE", "Идентификатор вида уже существует в старом хранилище.", 409)
                path = self._path("maps", "data-maps", map_id, "views", result["id"] + ".json", directory=False)
                try:
                    atomic_json(path, result, exclusive=True)
                except FileExistsError:
                    raise DataError("INVALID_STORAGE", "Путь нового вида уже занят.", 409) from None
            return result
        except sqlite3.DatabaseError as error:
            if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
                raise DataError("QUERY_BUDGET", "Запрос превысил бюджет 5 секунд. Сузьте область или фильтры.", 409) from None
            raise DataError("SNAPSHOT_CORRUPT", "База снимка повреждена. Обновите карту явно.", 409) from None
        finally:
            if connection is not None:
                connection.close()

    @map_operation
    def saved_view(self, map_id, view_id):
        """
        Read a historical authored view independently of the current map registration.

        Args:
            map_id: Validated fixed storage owner ID, including a registration explicitly forgotten.
            view_id:32-character saved-view ID; no arbitrary filename is accepted.
        Returns:
            The existing immutable view; it cannot drive current map queries without a registration.
        Raises:
            DataError: Missing view404, unsafe managed path409 or corrupt view409.
        """
        require(len(view_id) == 32 and all(char in "0123456789abcdef" for char in view_id), "VIEW_MISSING", "Вид не найден.", 404)
        self._identifier(map_id)
        path = self._path("maps", "data-maps", map_id, "views", view_id + ".json", directory=False)
        if not path.exists():
            path = self._path("data-maps", map_id, "views", view_id + ".json", directory=False)
        require(path.is_file(), "VIEW_MISSING", "Вид не найден.", 404)
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            raise DataError("SNAPSHOT_CORRUPT", "Сохранённый вид повреждён.", 409) from None

    def technical(self):
        """Размеры учитывают источники, БД, старые снимки и staging без чтения содержимого."""
        with self.lock:
            maps = []
            for item in self.maps.values():
                public = self.public(item)
                try:
                    map_id = self._identifier(item["id"])
                    directories = [self._path("maps", "data-maps", map_id), self._path("data-maps", map_id)]
                    size = sum(storage_bytes(directory) for directory in directories)
                    maps.append({**public, "sizeBytes": size, "storagePath": str(self.existing_directory(map_id))})
                except DataError as error:
                    maps.append({**public, "sizeBytes": None, "storagePath": None,
                                 "freshness": {"status": "unavailable", "code": error.code, "message": str(error)}})
            size = None
            if all(item["sizeBytes"] is not None for item in maps):
                size = sum(item["sizeBytes"] for item in maps)
            return {"storagePath": str(self.data_dir), "dataMapsPath": str(self.root), "maps": maps,
                    "sizeBytes": size, "buildPolicy": "explicit-request-only"}

    def request_stop(self):
        """Signal cancellation without waiting, and fence off new work during shutdown."""
        self.stop_requested.set()

    def close(self):
        """Cancel imports before waiting for their SQLite handles to be released."""
        self.request_stop()
        if self.executor:
            self.executor.shutdown(wait=True)
