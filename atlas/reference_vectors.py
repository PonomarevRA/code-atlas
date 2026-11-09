"""Отдельные локальные LanceDB поколения для справочников, без сетевых загрузок."""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from pathlib import Path
from sys import getsizeof
from uuid import uuid4

from .reference_documents import ReferenceError
from .reference_store import connect, now

MAX_REUSED_VECTORS = 2048
MAX_REUSED_VECTOR_BYTES = 32 * 1024 * 1024


class _EmbeddingCache:
    """Повторно использует соседние описания без накопления всего корпуса в RAM."""

    def __init__(self):
        """Начинает пустой кэш одного импорта; между корпусами он не сохраняется."""
        self.entries = OrderedDict()
        self.bytes = 0

    def get(self, text):
        """Продлевает жизнь используемого описания; вытеснение меняет лишь объём работы."""
        entry = self.entries.get(text)
        if entry is None:
            return None
        self.entries.move_to_end(text)
        return entry[0]

    def put(self, text, vector):
        """Ограничивает число и оценку размера Python-объектов, включая служебные записи."""
        size = getsizeof(text) + getsizeof(vector) + sum(getsizeof(value) for value in vector) + 192
        previous = self.entries.pop(text, None)
        if previous is not None:
            self.bytes -= previous[1]
        if size > MAX_REUSED_VECTOR_BYTES:
            return
        while self.entries and (len(self.entries) >= MAX_REUSED_VECTORS
                                or self.bytes + size > MAX_REUSED_VECTOR_BYTES):
            _, removed = self.entries.popitem(last=False)
            self.bytes -= removed[1]
        self.entries[text] = (vector, size)
        self.bytes += size


class ReferenceVectors:
    """Индексирует краткое представление каждого документа; полный текст хранится отдельно."""

    def __init__(self, directory: Path, embedder, bundled_dir: Path | None = None):
        """Отделяет производные векторы пользователя от встроенных read-only поколений."""
        self.directory = directory
        self.embedder = embedder
        self.bundled_dir = bundled_dir

    def _folder(self, pack_id: str) -> Path:
        """Ручной индекс имеет приоритет; встроенный используется без копирования и записи."""
        local = self.directory / pack_id
        if (local / "vectors.json").is_file() or self.bundled_dir is None:
            return local
        return self.bundled_dir / pack_id

    def status(self, pack_id: str) -> dict | None:
        """Читает опубликованный manifest без открытия LanceDB или модели."""
        path = self._folder(pack_id) / "vectors.json"
        if not path.exists():
            return None
        try:
            result = json.loads(path.read_text())
            if not isinstance(result, dict) or result.get("packId") != pack_id:
                raise ValueError("Unexpected pack identity")
            table = result.get("table", "")
            if not isinstance(table, str) or not table.startswith("generation_") or not table[11:].isalnum():
                raise ValueError("Unexpected table name")
            if not isinstance(result.get("model"), dict) or not result["model"].get("fingerprint"):
                raise ValueError("Missing model fingerprint")
        except (ValueError, OSError):
            raise ReferenceError("Invalid reference vector manifest", 500) from None
        return result

    def build(self, pack: dict, progress, cancelled=lambda: False) -> dict:
        """Строит все document summaries пакетами; атомарная публикация сохраняет старый индекс."""
        import lancedb
        import pyarrow as pa

        identity = self.embedder.identity()
        folder = self.directory / pack["id"]
        folder.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(folder / "lance")
        table_name = "generation_" + uuid4().hex
        table = db.create_table(table_name, schema=pa.schema([
            pa.field("id", pa.string()), pa.field("vector", pa.list_(pa.float32(), identity["dimensions"]))]))
        source = None
        temporary = None
        count = 0
        cached = _EmbeddingCache()
        embedded = 0
        try:
            source = connect(Path(pack["_directory"]) / "documents.sqlite")
            columns = {row["name"] for row in source.execute("PRAGMA table_info(documents)")}
            summary_column = "summary" if "summary" in columns else "'' AS summary"
            rows = source.execute(f"SELECT id,path,title,symbol,text,{summary_column} FROM documents ORDER BY id")
            while batch := rows.fetchmany(256):
                if cancelled():
                    raise ReferenceError("Index cancelled", 409)
                texts = []
                for doc in batch:
                    # Вектор — locator для API/страницы, не замена полного оригинального текста.
                    if pack["language"] in {"csharp", "dotnet", "efcore", "ef6"}:
                        # Одно описание разных overloads кодируется один раз. Версии и
                        # точные символы остаются в каталоге и не искажают semantic prose.
                        has_summary = doc["path"].lower().endswith(".xml") and "summary" in columns
                        candidate = (doc["summary"] if has_summary else doc["text"].split("Assembly versions:", 1)[0]).strip()[:4000]
                        if not candidate or candidate.replace("To be added.", "").strip() == "":
                            candidate = "Undocumented API member"
                    else:
                        candidate = doc["title"] + "\n" + doc["symbol"] + "\n" + doc["text"][:4000]
                    parts = self.embedder.split_text(candidate)
                    texts.append(parts[0]["text"])
                # Текущий пакет сохраняет свои векторы независимо от вытеснения LRU.
                # Одинаковые описания внутри пакета кодируются один раз.
                batch_vectors = {}
                missing = []
                for text in dict.fromkeys(texts):
                    vector = cached.get(text)
                    if vector is None:
                        missing.append(text)
                    else:
                        batch_vectors[text] = vector
                vectors = self.embedder.embed(missing) if missing else []
                if cancelled():
                    raise ReferenceError("Index cancelled", 409)
                if len(vectors) != len(missing):
                    raise ReferenceError("Embedding model returned incomplete batch", 500)
                for text, vector in zip(missing, vectors):
                    batch_vectors[text] = vector
                    cached.put(text, vector)
                embedded += len(missing)
                table.add([{"id": doc["id"], "vector": batch_vectors[text]} for doc, text in zip(batch, texts)])
                count += len(batch)
                progress(indexed=count, total=pack["documentCount"], embedded=embedded)
            if cancelled():
                raise ReferenceError("Index cancelled", 409)
            if count >= 512:
                sub_vectors = 32 if identity["dimensions"] % 32 == 0 else 1
                table.create_index(metric="cosine", num_partitions=max(1, min(256, count // 128)), num_sub_vectors=sub_vectors)
            result = {"packId": pack["id"], "table": table_name, "indexedAt": now(),
                      "documentCount": count, "coverage": "document-summaries", "model": identity,
                      "embeddedCount": embedded, "reusedCount": count - embedded}
            temporary = folder / (".vectors-" + uuid4().hex)
            temporary.write_text(json.dumps(result), encoding="utf-8")
            # Отмена последнего пакета или ANN-индекса не заменяет готовое поколение.
            if cancelled():
                raise ReferenceError("Index cancelled", 409)
            os.replace(temporary, folder / "vectors.json")
            return result
        except Exception:
            db.drop_table(table_name)
            raise
        finally:
            if source is not None:
                source.close()
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def search(self, pack: dict, query_vector: list[float], limit: int) -> list[str]:
        """Возвращает кандидатные ID, только если модель соответствует опубликованному индексу."""
        import lancedb
        metadata = self.status(pack["id"])
        if metadata is None:
            return []
        if metadata["model"]["fingerprint"] != self.embedder.identity()["fingerprint"]:
            raise ReferenceError("Reference vectors use a different model; rebuild explicitly", 409)
        folder = self._folder(pack["id"])
        table = lancedb.connect(folder / "lance").open_table(metadata["table"])
        return [row["id"] for row in table.search(query_vector).distance_type("cosine").limit(limit).to_list()]
