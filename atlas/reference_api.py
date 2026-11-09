"""Loopback API общих языковых справочников: чтение и явные операции writer."""
import asyncio
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .reference_documents import LANGUAGES, MAX_DOCUMENT_CHARS, ReferenceError
from .reference_store import validate_manifest


class ImportReference(BaseModel):
    """Только локальный ZIP и его provenance; сетевых URL загрузки в API нет."""
    model_config = ConfigDict(extra="forbid")
    archivePath: str = Field(min_length=1, max_length=4096)
    manifest: dict


class SearchReference(BaseModel):
    """Версия/edition задаются явно; machine identifiers не локализуются."""
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    language: str = Field(default="", max_length=30)
    version: str = Field(default="", max_length=100)
    packId: str = Field(default="", max_length=100)
    locale: str = Field(default="en", pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    mode: Literal["exact", "fulltext", "semantic", "hybrid"] = "hybrid"
    limit: int = Field(default=10, ge=1, le=20, strict=True)


def register_reference_routes(app: FastAPI, service) -> None:
    """Регистрирует отдельное пространство API без изменения снимков или Graphify."""
    @app.exception_handler(ReferenceError)
    async def reference_error(request, error):
        return JSONResponse({"error": str(error)}, status_code=error.status)

    @app.get("/api/language-references")
    def catalog():
        return service.catalog()

    @app.post("/api/language-references/import", status_code=202)
    def import_pack(body: ImportReference):
        metadata = validate_manifest(body.manifest)
        return service.submit("import", archive=Path(body.archivePath).expanduser(), metadata=metadata)

    @app.post("/api/language-references/search")
    async def search(body: SearchReference):
        if body.language and body.language not in LANGUAGES:
            raise ReferenceError("Unsupported language")
        if not body.query.strip():
            raise ReferenceError("Empty query")
        return await asyncio.to_thread(service.search, body.query, body.language, body.version, body.packId, body.locale, body.mode, body.limit)

    @app.post("/api/language-references/{pack_id}/index", status_code=202)
    def index(pack_id: str):
        return service.submit("index", pack_id=pack_id)

    @app.get("/api/language-references/jobs/{job_id}")
    def job(job_id: str):
        return service.job(job_id)

    @app.get("/api/language-references/{pack_id}/documents/{document_id}")
    def document(pack_id: str, document_id: str, offset: int = Query(0, ge=0, le=MAX_DOCUMENT_CHARS), limit: int = Query(12000, ge=1, le=16000)):
        return service.store.document(pack_id, document_id, offset, limit)
