"""Mod management and provider runtime UI are local application settings, never repository data."""
from hashlib import sha256
from .mod_models import ModManifest
import os
import subprocess
import sys
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from .mod_store import ModError, relative_asset
from .source_viewer import _read_regular_file
from .mod_wiki import ModWikiError, read_wiki_page, wiki_catalog


class ModActivation(BaseModel):
    """The current content fingerprint makes executable activation explicit."""
    fingerprint: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    enabled: bool | None
    model_config = {'extra':'forbid'}


class AnalysisAutoPolicy(BaseModel):
    """Глобальная автоматика касается только декларативных анализаторов хоста."""
    enabled: bool = Field(strict=True)
    model_config = {'extra': 'forbid'}


class RuntimeSelection(BaseModel):
    """An absolute interpreter path selects an already installed provider; this endpoint never downloads."""
    python: str | None = Field(default=None, max_length=4096)
    model_config = {'extra':'forbid'}


def register_mod_routes(app: FastAPI, store):
    """Serve only active mod assets, their grouping catalog and independent Graphify settings."""
    @app.exception_handler(ModError)
    async def mod_error(request,error):
        return JSONResponse({'error':str(error)},status_code=400)

    @app.exception_handler(ModWikiError)
    async def wiki_error(request, error):
        """
        Возвращает безопасное сообщение ошибки wiki с её HTTP-статусом.

        request — текущий запрос FastAPI; error — ModWikiError чтения страницы.
        Результат — JSON {error} с кодом 400, 404 или 409.
        """
        return JSONResponse({'error': str(error)}, status_code=error.status)

    @app.get('/api/mods')
    def list_mods():
        """Read manifests without importing executable mods or rebuilding any map."""
        return store.mods.catalog()

    @app.get('/api/mods/schema')
    def manifest_schema():
        """Publish the exact supported manifest contract, without executing mods."""
        return ModManifest.model_json_schema()

    @app.get('/api/mods/wiki')
    def list_wiki():
        """
        Возвращает каталог метаданных wiki без активации пакетов.

        Параметров нет; результат — JSON apiVersion, mods и truncated без текстов
        страниц. ModStore сохраняет обычное хеширование ресурсов при обнаружении.
        """
        return JSONResponse(wiki_catalog(store.mods), headers={'Cache-Control': 'no-store'})

    @app.get('/api/mods/{identifier}/wiki/{page_id}')
    def wiki_page(identifier: str, page_id: str, fingerprint: str | None = None):
        """
        Читает объявленную страницу текущей или запрошенной точной ревизии.

        identifier и page_id — ID мода и страницы; fingerprint — необязательная
        ревизия каталога. Возвращает JSON с text, format, fingerprint и source.
        ModWikiError преобразуется обработчиком в 400, 404 или 409.
        """
        result = read_wiki_page(store.mods, identifier, page_id, fingerprint)
        return JSONResponse(result, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.post('/api/mods/disable-all')
    def disable_all():
        """Emergency recovery remains available independently of executable frontend modules."""
        return store.mods.disable_all()

    @app.post('/api/mods/analysis-auto')
    def analysis_auto(body: AnalysisAutoPolicy):
        """Не запускает сборку; новые снимки применят выбранную политику."""
        return store.mods.set_analysis_auto(body.enabled)

    @app.post('/api/mods/{identifier}/activation')
    def activate_mod(identifier: str, body: ModActivation):
        """Enable or disable the displayed exact package version; original graph facts stay unchanged."""
        return store.mods.enable(identifier, body.fingerprint, body.enabled)

    @app.get('/api/mods/{identifier}/assets/{fingerprint}/{relative:path}')
    def asset(identifier: str, fingerprint: str, relative: str):
        """Versioned paths preserve relative ES module imports while blocking disabled/outside assets."""
        item=store.mods.active(identifier,fingerprint)
        path=relative_asset(relative)
        kinds={'.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2'}
        if relative not in item['assets'] or path.suffix not in kinds:
            raise ModError('Asset is not a declared frontend resource')
        content=_read_regular_file(item['root'],path,1024*1024)
        if sha256(content).hexdigest()!=item['assets'][relative]['hash']:
            raise ModError('Mod asset changed during read; reactivate the new version')
        return Response(content, media_type=kinds[path.suffix],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.post('/api/mods/open-directory')
    def open_directory():
        """Open only the dedicated user mod folder; arbitrary OS paths are not accepted."""
        path=store.mods.directory
        path.mkdir(parents=True,exist_ok=True)
        if sys.platform=='win32': os.startfile(str(path))
        else: subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return {'directory':str(path)}

    @app.get('/api/integrations/graphify')
    def graphify_runtime():
        """Read current vendor/runtime version without executing extraction or touching source projects."""
        return store.graphify_runtime.info()

    @app.post('/api/integrations/graphify/runtime')
    def select_runtime(body: RuntimeSelection):
        """Validate a separate installed Graphify runtime, then atomically update local settings."""
        try: return store.graphify_runtime.set(body.python)
        except ValueError as error: raise ModError(str(error)) from error
