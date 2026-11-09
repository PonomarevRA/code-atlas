"""Поколения локального LanceDB индекса с атомарным manifest и общим cache векторов."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from threading import RLock
import uuid

from .semantic_embeddings import SemanticIndexError

BATCH_SIZE = 32
MAX_VECTOR_CHUNKS = 6000
MAX_DOCUMENT_REFERENCES = 60000


def _json(value) -> str:
    """Стабильный JSON используется и для hash, и для сохранения полного chunk provenance."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    """Hash входных данных не раскрывает project/snapshot IDs в именах файлов или таблиц."""
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _vector(value, dimensions: int) -> list[float]:
    """Проверяет размерность и конечность до записи в Arrow или поиска LanceDB."""
    try:
        result = [float(number) for number in value]
    except (TypeError, ValueError, OverflowError) as error:
        raise SemanticIndexError("Модель вернула некорректный вектор.") from error
    if len(result) != dimensions or not all(math.isfinite(number) and abs(number) <= 3.4e38 for number in result):
        raise SemanticIndexError("Размерность или значения embedding-вектора некорректны.")
    if not any(number != 0 for number in result):
        raise SemanticIndexError("Модель вернула нулевой embedding-вектор.")
    return result


class IndexDatabase:
    """Читает только опубликованное поколение exact project/snapshot; не ищет старые снимки."""

    def __init__(self, data_dir: Path, embedder):
        """Создаёт лёгкий handle. LanceDB и embedding-модель загружаются при build/search."""
        self.directory = Path(data_dir) / "semantic"
        self.embedder = embedder
        self._database = None
        self._lock = RLock()
        self._connection_lock = RLock()

    def _manifest_path(self, project_id: str, snapshot_id: str) -> Path:
        """Точная пара IDs имеет отдельный manifest; пользовательские ID не входят в путь."""
        return self.directory / "manifests" / f"{_hash([project_id, snapshot_id])}.json"

    def _db(self):
        """Открывает только локальную директорию LanceDB, без remote embedding функций."""
        with self._connection_lock:
            if self._database is None:
                import lancedb
                self.directory.mkdir(parents=True, exist_ok=True)
                self._database = lancedb.connect(self.directory / "tables")
            return self._database

    def status(self, project_id: str, snapshot_id: str) -> dict | None:
        """Статус читает только manifest; не импортирует LanceDB и не вызывает identity()."""
        path = self._manifest_path(project_id, snapshot_id)
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise SemanticIndexError("Индекс документации повреждён. Пересоберите индекс документации.") from error
        self._validate_manifest(metadata)
        if metadata.get("projectId") != project_id or metadata.get("snapshotId") != snapshot_id:
            raise SemanticIndexError("Manifest не соответствует запрошенному снимку.")
        return metadata

    @staticmethod
    def _validate_manifest(metadata) -> None:
        """Проверяет сохранённый контракт до использования полей без загрузки базы или модели.

        Поддерживает прежнее поколение с векторами и новое с полными документами.
        Частично записанный или вручную повреждённый JSON не становится готовым индексом.
        """
        message = "Индекс документации повреждён. Пересоберите индекс документации."
        if not isinstance(metadata, dict):
            raise SemanticIndexError(message)
        schema = metadata.get("schemaVersion")
        table = metadata.get("table")
        model = metadata.get("model")
        if (type(schema) is not int or schema not in {1, 2}
                or not isinstance(table, str) or not re.fullmatch(r"generation_[0-9a-f]{32}", table)
                or not isinstance(model, dict)):
            raise SemanticIndexError(message)
        dimensions = model.get("dimensions")
        fingerprint = model.get("fingerprint")
        if (type(dimensions) is not int or not 1 <= dimensions <= 65536
                or not isinstance(fingerprint, str) or not fingerprint
                or metadata.get("dimensions") != dimensions
                or metadata.get("modelFingerprint") != fingerprint):
            raise SemanticIndexError(message)
        for field in ("chunkCount", "sourceChunkCount", "embeddedCount", "reusedCount"):
            value = metadata.get(field)
            if type(value) is not int or value < 0:
                raise SemanticIndexError(message)
        if metadata.get("embeddedCount") + metadata.get("reusedCount") != metadata["chunkCount"]:
            raise SemanticIndexError(message)
        warnings = metadata.get("warnings")
        if (not isinstance(metadata.get("truncated"), bool)
                or not isinstance(warnings, list)
                or any(not isinstance(warning, str) for warning in warnings)):
            raise SemanticIndexError(message)
        if schema == 2 or metadata.get("documentsAvailable"):
            if (metadata.get("documentsAvailable") is not True
                    or metadata.get("documentsTable") != table + "_documents"
                    or metadata.get("documentRefsTable") != table + "_refs"):
                raise SemanticIndexError(message)
            for field in ("documentCount", "documentReferenceCount"):
                value = metadata.get(field)
                if type(value) is not int or value < 0:
                    raise SemanticIndexError(message)

    def _identity(self) -> dict:
        """Проверяет контракт провайдера перед использованием model fingerprint."""
        identity = self.embedder.identity()
        dimensions = identity.get("dimensions")
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or not 1 <= dimensions <= 65536:
            raise SemanticIndexError("Embedding provider сообщил некорректную размерность.")
        if not isinstance(identity.get("fingerprint"), str) or not identity["fingerprint"]:
            raise SemanticIndexError("Embedding provider не сообщил fingerprint модели.")
        return identity

    def _split_chunks(self, corpus: dict) -> tuple[list[dict], bool]:
        """Разделяет captured text по token budget, сохраняя исходный диапазон и связи каждого chunk."""
        chunks, seen = [], set()
        for chunk in corpus.get("chunks", []):
            if not isinstance(chunk, dict) or chunk.get("kind") not in {"comment", "documentation"}:
                raise SemanticIndexError("Некорректный chunk корпуса.", 400)
            parent_id, text = chunk.get("id"), chunk.get("text")
            if not isinstance(parent_id, str) or not parent_id or parent_id in seen or not isinstance(text, str):
                raise SemanticIndexError("Некорректный или повторный ID chunk корпуса.", 400)
            seen.add(parent_id)
            if not text.strip():
                continue
            splitter = getattr(self.embedder, "split_text", None)
            if splitter is None:
                raise SemanticIndexError("Embedding provider не поддерживает проверку token budget.")
            parts = splitter(text, max_tokens=126)
            if not parts or "".join(part["text"] for part in parts) != text:
                raise SemanticIndexError("Разделение embedding-текста потеряло исходный текст.")
            for number, part in enumerate(parts):
                if len(chunks) >= MAX_VECTOR_CHUNKS:
                    return chunks, True
                if not isinstance(part.get("tokenCount"), int) or not 0 <= part["tokenCount"] <= 126:
                    raise SemanticIndexError("Разделение текста превысило token budget.")
                result = dict(chunk)
                result.update(id=parent_id if len(parts) == 1 else f"{parent_id}:part:{number + 1}", text=part["text"], parentChunkId=parent_id, textOffsetStart=part["start"], textOffsetEnd=part["end"], tokenCount=part["tokenCount"])
                document_start = chunk.get("documentOffsetStart")
                if isinstance(document_start, int) and not isinstance(document_start, bool):
                    result.update(sourceChunkDocumentOffsetStart=document_start,
                                  sourceChunkDocumentOffsetEnd=chunk.get("documentOffsetEnd"),
                                  documentOffsetStart=document_start + part["start"],
                                  documentOffsetEnd=document_start + part["end"])
                # XML/comments нормализованы при capture: новые line numbers были бы ложной точностью.
                chunks.append(result)
        return chunks, False

    def _schema(self, dimensions: int, *, cache: bool = False):
        """Фиксированная float32 размерность нужна LanceDB даже для пустого корпуса."""
        import pyarrow as pa
        fields = [pa.field("key", pa.string()), pa.field("vector", pa.list_(pa.float32(), dimensions))]
        if not cache:
            fields.extend([pa.field("id", pa.string()), pa.field("kind", pa.string()), pa.field("chunkJson", pa.string())])
        return pa.schema(fields)

    def _cache(self, identity: dict):
        """Cache разделён по fingerprint, поэтому несовместимые модели никогда не смешиваются."""
        name = "vectors_" + _hash(identity)[0:32]
        db = self._db()
        try:
            return db.open_table(name)
        except FileNotFoundError:
            return db.create_table(name, schema=self._schema(identity["dimensions"], cache=True))
        except ValueError as error:
            # LanceDB сообщает missing table как ValueError, сохраняя ошибки иных причин.
            if "not found" not in str(error).lower() and "does not exist" not in str(error).lower():
                raise
            return db.create_table(name, schema=self._schema(identity["dimensions"], cache=True))

    def _batch_vectors(self, chunks: list[dict], identity: dict, cache) -> tuple[list[dict], int]:
        """Повторяющийся текст кодируется один раз, provenance хранится отдельно в generation table."""
        keys = [_hash([identity["fingerprint"], chunk["text"]]) for chunk in chunks]
        unique = dict(zip(keys, [chunk["text"] for chunk in chunks]))
        expression = "key IN (" + ",".join(f"'{key}'" for key in unique) + ")"
        existing = cache.search().where(expression).limit(len(unique)).to_list()
        vectors = {row["key"]: _vector(row["vector"], identity["dimensions"]) for row in existing}
        missing = [key for key in unique if key not in vectors]
        if missing:
            generated = self.embedder.embed([unique[key] for key in missing])
            if len(generated) != len(missing):
                raise SemanticIndexError("Модель вернула не все embedding-векторы.")
            additions = []
            for key, value in zip(missing, generated):
                vectors[key] = _vector(value, identity["dimensions"])
                additions.append({"key": key, "vector": vectors[key]})
            cache.add(additions)
        rows = []
        for key, chunk in zip(keys, chunks):
            rows.append({"key": key, "vector": vectors[key], "id": chunk["id"], "kind": chunk["kind"], "chunkJson": _json(chunk)})
        return rows, len(missing)

    def _publish(self, path: Path, metadata: dict):
        """Atomic replace публикует полностью записанное поколение; fsync завершает запись manifest."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(_json(metadata))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _metadata(self, project_id, snapshot_id, corpus, corpus_hash, identity, table, chunks, embedded, vector_truncated) -> dict:
        """Manifest отличает captured corpus от token-split индекса и явно сообщает обе границы полноты."""
        warnings = [str(warning)[:200] for warning in corpus.get("warnings", [])[:10]]
        if vector_truncated:
            warnings = [f"Достигнут лимит {MAX_VECTOR_CHUNKS} embedding-фрагментов; индекс содержит часть корпуса.", *warnings][:10]
        return {
            "schemaVersion": 1, "status": "ready",
            "projectId": project_id, "snapshotId": snapshot_id, "table": table,
            "model": identity, "modelFingerprint": identity["fingerprint"], "dimensions": identity["dimensions"],
            "corpusHash": corpus_hash,
            "chunkCount": len(chunks), "sourceChunkCount": len(corpus["chunks"]),
            "embeddedCount": embedded, "reusedCount": len(chunks) - embedded,
            "capturedAt": corpus.get("capturedAt"), "indexedAt": datetime.now(timezone.utc).isoformat(),
            "extractorVersion": corpus.get("extractorVersion"), "sourceKind": corpus.get("sourceKind"),
            "truncated": bool(corpus.get("truncated")) or vector_truncated,
            "stats": corpus.get("stats", {}),
            "limits": {**corpus.get("limits", {}), "payloadTokens": 126, "vectorChunkLimit": MAX_VECTOR_CHUNKS, "tokenLimitGuaranteed": True},
            "warnings": warnings,
        }

    def _document_tables(self, corpus: dict, generation: str) -> tuple[dict, list[str]]:
        """Полные тексты и лёгкие source refs записываются отдельно от embedding chunks."""
        documents = corpus.get("documents")
        if documents is None:
            return {}, []
        if not isinstance(documents, list) or len(documents) > 2000:
            raise SemanticIndexError("Некорректные captured documents.", 400)
        import pyarrow as pa
        rows = []
        references = []
        seen_ids = set()
        seen_references = set()
        warnings = []
        total_bytes = 0
        for document in documents:
            if not isinstance(document, dict):
                raise SemanticIndexError("Некорректный captured document.", 400)
            identifier, text = document.get("id"), document.get("text")
            if not isinstance(identifier, str) or not identifier or identifier in seen_ids or not isinstance(text, str):
                raise SemanticIndexError("Некорректный или повторный document ID.", 400)
            total_bytes += len(text.encode("utf-8"))
            if len(text) > 128000 or total_bytes > 16 * 1024 * 1024:
                raise SemanticIndexError("Captured documents превышают бюджет.", 400)
            seen_ids.add(identifier)
            summary = {key: value for key, value in document.items() if key not in {"text", "sourceLinks", "nodeIds", "fileNodeIds"}}
            summary.update(
                preview=text[:400],
                totalChars=len(text),
                nodeIds=document.get("nodeIds", [])[:12],
                nodeIdsTruncated=len(document.get("nodeIds", [])) > 12,
                sourceLinkCount=len(document.get("sourceLinks", [])),
            )
            rows.append({"id": identifier, "summaryJson": _json(summary), "documentJson": _json(document)})
            links = [{"nodeIds": document.get("nodeIds", []), "association": document.get("association", "file"),
                      "scope": "symbol" if document.get("association") in {"declaration", "snapshot-rationale"} else "file",
                      "file": document.get("file"), "line": document.get("line")}]
            links.extend(document.get("sourceLinks", []))
            if document.get("fileNodeIds"):
                links.append({"nodeIds": document["fileNodeIds"], "association": "file", "scope": "file",
                              "file": document.get("file"), "line": document.get("line")})
            for link in links:
                for node_id in link.get("nodeIds", []):
                    key = identifier, node_id, link.get("association"), link.get("scope")
                    if key in seen_references:
                        continue
                    if len(references) >= MAX_DOCUMENT_REFERENCES:
                        if not warnings:
                            warnings.append("Документальные привязки усечены по лимиту references.")
                        continue
                    seen_references.add(key)
                    references.append({
                        "node_id": node_id,
                        "document_id": identifier,
                        "association": str(link.get("association", "source-link")),
                        "scope": str(link.get("scope", "file")),
                        "evidence_json": _json({"file": link.get("file"), "line": link.get("line")}),
                    })
        db = self._db()
        names = {"documentsTable": generation + "_documents", "documentRefsTable": generation + "_refs"}
        document_schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("summaryJson", pa.string()),
            pa.field("documentJson", pa.string()),
        ])
        reference_fields = ("node_id", "document_id", "association", "scope", "evidence_json")
        reference_schema = pa.schema([pa.field(name, pa.string()) for name in reference_fields])
        schemas = [document_schema, reference_schema]
        created = []
        try:
            for name, schema, values in zip(names.values(), schemas, (rows, references)):
                table = db.create_table(name, schema=schema)
                created.append(name)
                for start in range(0, len(values), BATCH_SIZE):
                    table.add(values[start:start + BATCH_SIZE])
        except Exception:
            for name in created:
                db.drop_table(name)
            raise
        return {**names, "documentCount": len(rows), "documentReferenceCount": len(references),
                "documentsAvailable": True}, warnings

    def build(self, project_id: str, snapshot_id: str, corpus: dict, assert_current, force: bool = False) -> dict:
        """Публикует generation только после полной записи и последней проверки актуальности capture."""
        with self._lock:
            if not isinstance(corpus, dict) or not isinstance(corpus.get("chunks"), list):
                raise SemanticIndexError("Некорректный captured corpus.", 400)
            if corpus.get("snapshotId") not in (None, snapshot_id):
                raise SemanticIndexError("Корпус не соответствует снимку индекса.", 409)
            identity = self._identity()
            corpus_hash = _hash(corpus)
            old = self.status(project_id, snapshot_id)
            if not force and old and old.get("corpusHash") == corpus_hash and old.get("model") == identity:
                assert_current()
                return old
            chunks, vector_truncated = self._split_chunks(corpus)
            name = "generation_" + uuid.uuid4().hex
            db = self._db()
            table = db.create_table(name, schema=self._schema(identity["dimensions"]))
            embedded = 0
            document_metadata = {}
            try:
                cache = self._cache(identity)
                for start in range(0, len(chunks), BATCH_SIZE):
                    rows, count = self._batch_vectors(chunks[start:start + BATCH_SIZE], identity, cache)
                    table.add(rows)
                    embedded += count
                document_metadata, document_warnings = self._document_tables(corpus, name)
                metadata = self._metadata(project_id, snapshot_id, corpus, corpus_hash, identity, name, chunks, embedded, vector_truncated)
                metadata.update(document_metadata)
                metadata["schemaVersion"] = 2 if document_metadata else 1
                if document_warnings:
                    metadata["warnings"] = [*document_warnings, *metadata["warnings"]][:10]
                    metadata["truncated"] = True
                assert_current()
                self._publish(self._manifest_path(project_id, snapshot_id), metadata)
                return metadata
            except Exception:
                db.drop_table(name)
                for key in ("documentsTable", "documentRefsTable"):
                    if key in document_metadata:
                        db.drop_table(document_metadata[key])
                raise

    def search(self, project_id: str, snapshot_id: str, query: str, limit: int = 8, kind: str = "all", *, metadata: dict | None = None) -> dict:
        """Ищет только captured text точного снимка; файлы проекта при запросе не читаются."""
        if kind not in {"all", "comment", "documentation"}:
            raise SemanticIndexError("Неизвестный вид semantic-документа.", 400)
        if not isinstance(query, str) or not query.strip():
            raise SemanticIndexError("Введите непустой поисковый запрос.", 400)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise SemanticIndexError("Некорректный лимит поиска.", 400)
        # Неизменяемое опубликованное поколение доступно, пока writer строит следующее.
        # Atomic replace гарантирует цельный manifest; тяжёлая build-lock здесь не нужна.
        metadata = metadata or self.status(project_id, snapshot_id)
        if metadata is None:
            raise SemanticIndexError("Индекс этого снимка не построен.", 409)
        if metadata.get("projectId") != project_id or metadata.get("snapshotId") != snapshot_id:
            raise SemanticIndexError("Поколение не соответствует запрошенному снимку.", 409)
        identity = self._identity()
        if metadata.get("model") != identity:
            raise SemanticIndexError("Модель изменилась: индекс снимка необходимо пересобрать.", 409)
        if not metadata["chunkCount"]:
            return {"index": metadata, "hits": []}
        vector = _vector(self.embedder.query(query), identity["dimensions"])
        table = self._db().open_table(metadata["table"])
        search = table.search(vector).distance_type("cosine")
        if kind != "all":
            search = search.where(f"kind = '{kind}'", prefilter=True)
        hits = []
        for row in search.limit(limit).to_list():
            hits.append({**json.loads(row["chunkJson"]), "distance": float(row["_distance"])})
        return {"index": metadata, "hits": hits}

    def published_generation(self, project_id: str, snapshot_id: str) -> dict:
        """Закрепляет один immutable manifest; документы читаются без identity/ONNX."""
        metadata = self.status(project_id, snapshot_id)
        if metadata is None:
            raise SemanticIndexError("Индекс этого снимка не построен.", 409)
        if not metadata.get("documentsAvailable"):
            raise SemanticIndexError("Индекс не хранит полные документы. Обновите индекс документации.", 409)
        return metadata

    def _generation(self, project_id, snapshot_id, metadata):
        """Pinned manifest не может перейти в другую пару project/snapshot."""
        metadata = metadata or self.published_generation(project_id, snapshot_id)
        if metadata.get("projectId") != project_id or metadata.get("snapshotId") != snapshot_id:
            raise SemanticIndexError("Поколение не соответствует запрошенному снимку.", 409)
        if not metadata.get("documentsAvailable"):
            raise SemanticIndexError("Обновите индекс для чтения полных документов.", 409)
        return metadata

    @staticmethod
    def _literal(value):
        """SQL literal экранируется, raw IDs никогда не интерполируются как выражения."""
        return "'" + str(value).replace("'", "''") + "'"

    def documents_for_nodes(self, project_id, snapshot_id, node_ids, limit=20, *, metadata=None):
        """Читает только lightweight refs/summaries exact generation, без поиска по живым файлам."""
        metadata = self._generation(project_id, snapshot_id, metadata)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise SemanticIndexError("Некорректный лимит документов.", 400)
        node_ids = list(dict.fromkeys(node_ids))[:25]
        if not node_ids:
            return []
        expression = "node_id IN (" + ",".join(self._literal(node_id) for node_id in node_ids) + ")"
        table = self._db().open_table(metadata["documentRefsTable"])
        references = table.search().where(expression).limit(MAX_DOCUMENT_REFERENCES).to_list()
        grouped = {}
        for reference in references:
            grouped.setdefault(reference["document_id"], []).append({
                "nodeId": reference["node_id"], "association": reference["association"],
                "scope": reference["scope"], "evidence": json.loads(reference["evidence_json"]),
            })
        identifiers = sorted(grouped)[:limit]
        if not identifiers:
            return []
        expression = "id IN (" + ",".join(self._literal(identifier) for identifier in identifiers) + ")"
        documents = self._db().open_table(metadata["documentsTable"])
        rows = documents.search().select(["id", "summaryJson"]).where(expression).limit(limit).to_list()
        return [{**json.loads(row["summaryJson"]), "matches": grouped[row["id"]][:25]} for row in sorted(rows, key=lambda item: item["id"])]

    def document(self, project_id, snapshot_id, document_id, *, metadata=None):
        """Возвращает bounded captured document по opaque ID; путь проекта не читается."""
        metadata = self._generation(project_id, snapshot_id, metadata)
        table = self._db().open_table(metadata["documentsTable"])
        rows = table.search().where("id = " + self._literal(document_id)).limit(1).to_list()
        if not rows:
            raise SemanticIndexError("Документ не найден в индексе этого снимка.", 404)
        return json.loads(rows[0]["documentJson"])

    def close(self):
        """Освобождает handle соединения; опубликованные поколения остаются на диске."""
        with self._connection_lock:
            self._database = None
