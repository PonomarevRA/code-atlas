"""Неизменяемые версии общих справочников с точным и полнотекстовым поиском."""
from __future__ import annotations

from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
from uuid import uuid4
import zipfile

from .reference_documents import LANGUAGES, ReferenceError, documents, eligible, file_limit, without_generics
from .reference_framework import framework_members

MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_EXPANDED_BYTES = 3 * 1024 * 1024 * 1024
MAX_FILES = 300000
EXTRACTOR_VERSION = "language-references-v8-xml-sections"


def now() -> str:
    """Дата публикации корпуса, независимая от снимков проектов."""
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    """Открывает опубликованную БД только для чтения, не создавая её при ошибке пути."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def validate_manifest(value: dict) -> dict:
    """Проверяет версию, язык, происхождение и отдельную локаль импортируемого корпуса."""
    if not isinstance(value, dict) or value.get("language") not in LANGUAGES:
        raise ReferenceError("Unsupported language")
    result = {"schemaVersion": 1, "language": value["language"], "extractorVersion": EXTRACTOR_VERSION}
    for key, maximum in (("version", 100), ("edition", 100), ("title", 200), ("sourceUrl", 2000), ("license", 500)):
        text = value.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > maximum or any(ord(c) < 32 for c in text):
            raise ReferenceError(f"Invalid manifest field: {key}")
        result[key] = text.strip()
    if not result["sourceUrl"].startswith("https://"):
        raise ReferenceError("Expected HTTPS provenance URL (never fetched by Atlas)")
    locale = value.get("locale", "en")
    if not isinstance(locale, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", locale):
        raise ReferenceError("Invalid locale")
    result["locale"] = locale
    includes = value.get("includes", [])
    if not isinstance(includes, list) or len(includes) > 20 or any(not isinstance(p, str) or not p or len(p) > 500 for p in includes):
        raise ReferenceError("Invalid include patterns")
    result["includes"] = includes
    framework = value.get("framework", "")
    if not isinstance(framework, str) or framework and (result["language"] != "dotnet" or not re.fullmatch(r"net-\d+\.\d+", framework)):
        raise ReferenceError("Invalid .NET framework selector")
    if framework:
        version = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.\d+)?", result["version"])
        if version is None or framework != f"net-{version.group(1)}.{version.group(2) or '0'}":
            raise ReferenceError("Framework does not match declared .NET version")
        result["framework"] = framework
    return result


def _archive_hash(path: Path) -> str:
    """Вычисляет fingerprint архива потоково, не загружая его целиком в память."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def import_archive(archive: Path, metadata: dict, destination: Path, progress=lambda **kw: None) -> dict:
    """Импортирует ZIP в staging и атомарно публикует новую редакцию, сохраняя прежние.

    Архив никогда не распаковывается в файловую систему; читаются выбранные страницы.
    Источник, license и archiveHash остаются в manifest. Сбой удаляет только staging.
    """
    metadata = validate_manifest(metadata)
    archive = Path(archive)
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ReferenceError("Expected local ZIP up to 1 GiB")
    fingerprint = _archive_hash(archive)
    identity = hashlib.sha256(json.dumps([metadata, fingerprint], sort_keys=True).encode()).hexdigest()[:32]
    pack_id = "reference-" + identity
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / pack_id
    if (final / "manifest.json").is_file():
        return json.loads((final / "manifest.json").read_text())
    staging = destination / (".import-" + uuid4().hex)
    staging.mkdir()
    connection = None
    try:
        connection = sqlite3.connect(staging / "documents.sqlite")
        connection.executescript("""
            CREATE TABLE documents(id TEXT PRIMARY KEY, path TEXT, title TEXT, symbol TEXT, signature TEXT, text TEXT, summary TEXT, source_url TEXT);
            CREATE TABLE aliases(alias TEXT, document_id TEXT, PRIMARY KEY(alias,document_id));
            CREATE VIRTUAL TABLE fulltext USING fts5(title,symbol,text,content='documents',content_rowid='rowid');
        """)
        count, files, expanded, skipped = 0, 0, 0, 0
        licenses = {}
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            if len(entries) > MAX_FILES:
                raise ReferenceError("Archive contains too many entries")
            framework = metadata.get("framework", "")
            allowed_members = framework_members(package, framework)
            for info in entries:
                path = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                    raise ReferenceError("Unsafe archive entry")
                expanded += info.file_size
                if expanded > MAX_EXPANDED_BYTES:
                    raise ReferenceError("Archive exceeds expanded size limit")
                if info.is_dir():
                    continue
                if (PurePosixPath(info.filename).name.lower().startswith(("license", "copying", "notice"))
                        and info.file_size <= 1024 * 1024 and len(licenses) < 100):
                    licenses[info.filename] = package.read(info).decode("utf-8", errors="replace")
                selected = (any(fnmatch.fnmatchcase(info.filename, pattern) for pattern in metadata["includes"])
                            if metadata["includes"] else eligible(info.filename, metadata["language"]))
                if not selected:
                    skipped += 1
                    continue
                if mode & 0o170000 == 0o120000:
                    raise ReferenceError("Unsafe archive entry: selected symlink")
                maximum = file_limit(info.filename, metadata["language"])
                if info.file_size > maximum:
                    raise ReferenceError(f"File exceeds {maximum // 1024 // 1024} MiB: {info.filename}")
                for doc in documents(info.filename, package.read(info), metadata["language"], framework):
                    if allowed_members is not None and not allowed_members.intersection(doc["aliases"]):
                        continue
                    if not doc["text"].strip():
                        continue
                    connection.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                                       (*tuple(doc[k] for k in ("id", "path", "title", "symbol", "signature", "text", "summary")), doc.get("sourceUrl", metadata["sourceUrl"])))
                    aliases = {alias.casefold().removeprefix("m:").removeprefix("t:") for alias in doc["aliases"] if alias}
                    aliases.add(doc["symbol"].split(".")[-1].split("<")[0].casefold())
                    connection.executemany("INSERT OR IGNORE INTO aliases VALUES (?,?)", [(a, doc["id"]) for a in aliases])
                    count += 1
                files += 1
                if files % 100 == 0:
                    connection.commit()
                    progress(files=files, documents=count)
        if not count:
            raise ReferenceError("No documentation matched the selected corpus")
        progress(files=files, documents=count, phase="fulltext-index")
        connection.execute("INSERT INTO fulltext(fulltext) VALUES ('rebuild')")
        connection.commit()
        connection.close()
        connection = None
        if _archive_hash(archive) != fingerprint:
            raise ReferenceError("Archive changed during import; retry with a stable file", 409)
        result = {**metadata, "id": pack_id, "archiveHash": fingerprint, "importedAt": now(),
                  "documentCount": count, "fileCount": files, "skippedFiles": skipped,
                  "sizeBytes": (staging / "documents.sqlite").stat().st_size,
                  "coverage": "all-matching-files", "offline": True}
        (staging / "licenses.json").write_text(json.dumps(licenses, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.rename(staging, final)
        return result
    except (zipfile.BadZipFile, sqlite3.Error, OSError, RuntimeError) as error:
        raise ReferenceError(f"Import failed: {error}") from error
    finally:
        if connection is not None:
            connection.close()
        if staging.exists():
            shutil.rmtree(staging)


class ReferenceStore:
    """Читает встроенные и добавленные версии; открытие страницы не строит индексы."""

    def __init__(self, data_dir: Path, bundled_dir: Path | None = None):
        """Разделяет read-only ресурсы приложения и пользовательские ручные дополнения."""
        self.directory = Path(data_dir) / "language-references"
        self.bundled_dir = bundled_dir

    def catalog(self) -> list[dict]:
        """Компактный каталог опубликованных корпусов без чтения текстов и ONNX."""
        result = {}
        for directory in (self.bundled_dir, self.directory):
            if directory is None or not directory.is_dir():
                continue
            for path in sorted(directory.glob("reference-*/manifest.json"))[:512]:
                if path.parent.is_symlink() or path.is_symlink():
                    continue
                try:
                    pack = json.loads(path.read_text(encoding="utf-8"))
                    validate_manifest(pack)
                    if not re.fullmatch(r"reference-[0-9a-f]{32}", pack["id"]) or path.parent.name != pack["id"]:
                        continue
                    if not (path.parent / "documents.sqlite").is_file():
                        continue
                    result[pack["id"]] = {**pack, "bundled": directory == self.bundled_dir, "_directory": str(path.parent)}
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(result.values(), key=lambda p: (p["language"], p["importedAt"], p["id"]), reverse=True)

    def pack(self, pack_id: str) -> dict:
        """Разрешает ID только по каталогу; пользовательский путь не становится SQL-путём."""
        pack = next((p for p in self.catalog() if p["id"] == pack_id), None)
        if pack is None:
            raise ReferenceError("Reference edition not found", 404)
        return pack

    @staticmethod
    def public(pack: dict) -> dict:
        """Не раскрывает внутренний путь; provenance и версия доступны каждому ответу."""
        return {k: v for k, v in pack.items() if not k.startswith("_")}

    def search_pack(self, pack: dict, query: str, mode: str, limit: int) -> list[dict]:
        """Точное имя имеет приоритет над BM25; LIKE с wildcard не применяется."""
        database = connect(Path(pack["_directory"]) / "documents.sqlite")
        try:
            fields = {row["name"] for row in database.execute("PRAGMA table_info(documents)")}
            has_summary = "summary" in fields
            source = "d.source_url" if "source_url" in fields else "''"
            excerpt = "CASE WHEN length(d.summary)>0 THEN d.summary ELSE d.text END" if has_summary else "d.text"
            scope = "CASE WHEN length(d.summary)>0 THEN 'author-summary' ELSE 'document-head' END" if has_summary else "'document-head'"
            columns = f"d.id,d.path,d.title,d.symbol,d.signature,{source} AS source_url,substr({excerpt},1,1000) AS text,length({excerpt}) AS text_length,{scope} AS excerpt_scope"
            alias = without_generics(query.strip().casefold().removesuffix("()").removeprefix("m:").removeprefix("t:"))
            rows = database.execute(f"SELECT {columns}, 0 AS score FROM aliases a JOIN documents d ON d.id=a.document_id WHERE a.alias=? ORDER BY d.signature,d.id LIMIT ?",
                                    (alias, limit)).fetchall()
            if not rows and pack["language"] in {"dotnet", "csharp", "efcore", "ef6"}:
                # DocFX использует List`1.Count; старые индексы имеют alias List.Count.
                # Полный DocId перегрузки проверен первым; fallback не скрывает точный match.
                reference_alias = re.sub(r"^(?:[tmpfeon]:|overload:)", "", alias)
                reference_alias = re.sub(r"`{1,2}\d+", "", reference_alias)
                if reference_alias != alias:
                    rows = database.execute(f"SELECT {columns}, 0 AS score FROM aliases a JOIN documents d ON d.id=a.document_id WHERE a.alias=? ORDER BY d.signature,d.id LIMIT ?",
                                            (reference_alias, limit)).fetchall()
            match = "exact"
            if not rows and mode != "exact":
                terms = re.findall(r"[\w]+", query, re.UNICODE)[:32]
                if not terms:
                    return []
                expression = " OR ".join('"' + word + '"' for word in terms)
                rows = database.execute(f"SELECT {columns}, bm25(fulltext,5,10,1) AS score FROM fulltext JOIN documents d ON d.rowid=fulltext.rowid WHERE fulltext MATCH ? ORDER BY score,d.id LIMIT ?",
                                        (expression, limit)).fetchall()
                match = "fulltext"
            result = []
            for row in rows:
                result.append({"documentId": row["id"], "packId": pack["id"], "title": row["title"],
                               "symbol": row["symbol"], "signature": row["signature"], "excerpt": row["text"][:1000],
                               "excerptTruncated": row["text_length"] > 1000, "match": match,
                               "excerptScope": row["excerpt_scope"],
                               "version": pack["version"], "edition": pack["edition"], "locale": pack["locale"],
                               "sourceUrl": row["source_url"] or pack["sourceUrl"], "sourcePath": row["path"], "score": row["score"]})
            return result
        finally:
            database.close()

    def document(self, pack_id: str, doc_id: str, offset: int = 0, limit: int = 12000) -> dict:
        """Постраничное чтение полного документа; оригинал не заменяется кратким embedding-текстом."""
        pack = self.pack(pack_id)
        database = connect(Path(pack["_directory"]) / "documents.sqlite")
        try:
            fields = {row["name"] for row in database.execute("PRAGMA table_info(documents)")}
            has_summary = "summary" in fields
            source = "source_url" if "source_url" in fields else "''"
            summary = "substr(summary,1,1000)" if has_summary else "''"
            summary_truncated = "length(summary)>1000" if has_summary else "0"
            row = database.execute(f"SELECT id,path,title,symbol,signature,{source} AS sourceUrl,{summary} AS summary,{summary_truncated} AS summaryTruncated,substr(text,?,?) AS text,length(text) AS totalChars FROM documents WHERE id=?",
                                   (offset + 1, limit, doc_id)).fetchone()
            if row is None:
                raise ReferenceError("Document not found", 404)
            result = dict(row)
            result["sourceUrl"] = result["sourceUrl"] or pack["sourceUrl"]
            total = result["totalChars"]
            result.update(summaryTruncated=bool(result["summaryTruncated"]), offset=offset, nextOffset=offset + limit if offset + limit < total else None,
                          pack=self.public(pack), sourceKind="language-reference-corpus")
            return result
        finally:
            database.close()

    def corresponding_document(self, pack: dict, hit: dict) -> dict:
        """Разрешает перевод по ID или однозначной паре API/сигнатура, сохраняя overload."""
        try:
            return self.document(pack["id"], hit["documentId"], limit=1000)
        except ReferenceError as error:
            if error.status != 404 or not hit["signature"]:
                raise
        database = connect(Path(pack["_directory"]) / "documents.sqlite")
        try:
            rows = database.execute("SELECT id FROM documents WHERE symbol=? AND signature=? LIMIT 2",
                                    (hit["symbol"], hit["signature"])).fetchall()
        finally:
            database.close()
        if len(rows) != 1:
            raise ReferenceError("Matching translation not found", 404)
        return self.document(pack["id"], rows[0]["id"], limit=1000)
