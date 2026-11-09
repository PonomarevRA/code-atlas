"""Отдельный HTTP namespace документации зависимостей проекта."""
import asyncio
from typing import Literal

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from .reference_documents import MAX_DOCUMENT_CHARS, ReferenceError


class LibraryCapture(BaseModel):
    """Пустой manifest выбирает все найденные csproj; путь ограничен зарегистрированным root."""
    model_config = ConfigDict(extra='forbid')
    manifest: str = Field(default='', max_length=1000)


class LibrarySearch(BaseModel):
    """Поиск работает по последнему явному capture; документация не восстанавливает зависимости."""
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=500)
    packId: str = Field(default='', max_length=100)
    locale: str = Field(default='en', pattern=r'^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$')
    mode: Literal['exact', 'fulltext'] = 'fulltext'
    limit: int = Field(default=10, strict=True, ge=1, le=20)


def register_library_routes(app, service):
    """Регистрация не обходит файловую систему; чтение и refresh имеют разные endpoints."""
    prefix = '/api/projects/{project_id}/library-documentation'

    @app.get(prefix)
    def catalog(project_id: str):
        return service.catalog(project_id)

    @app.get(prefix + '/sources')
    async def sources(project_id: str, manifest: str = Query('', max_length=1000)):
        return await asyncio.to_thread(service.discover, project_id, manifest)

    @app.post(prefix + '/capture', status_code=202)
    def capture(project_id: str, body: LibraryCapture):
        return service.submit(project_id, body.manifest)

    @app.get(prefix + '/jobs/{job_id}')
    def job(project_id: str, job_id: str):
        return service.job(project_id, job_id)

    @app.post(prefix + '/search')
    async def search(project_id: str, body: LibrarySearch):
        if not body.query.strip():
            raise ReferenceError('Empty query')
        return await asyncio.to_thread(service.search, project_id, body.query, body.packId, body.locale, body.mode, body.limit)

    @app.get(prefix + '/{pack_id}/documents/{document_id}')
    async def document(project_id: str, pack_id: str, document_id: str,
                       offset: int = Query(0, ge=0, le=MAX_DOCUMENT_CHARS), limit: int = Query(12000, ge=1, le=16000)):
        return await asyncio.to_thread(service.document, project_id, pack_id, document_id, offset, limit)
