"""Самостоятельные HTTP-маршруты карт данных и технического каталога."""

import os
from pathlib import Path
import subprocess
import sys

from fastapi import Query
from fastapi.responses import JSONResponse, Response

from .data_graph import schema_graph
from .project_storage import project_directories
from .data_models import DataError, DataInput, DataQuery, require
from .data_sources import discover, storage_bytes
from .data_storage import encode


def register_data_routes(app, store, data_maps):
    """Регистрация не сканирует репозитории, не запускает worker и не строит карту."""

    @app.exception_handler(DataError)
    async def data_error(request, error):
        """Устойчивый код отличает stale, mismatch, предел и неверный источник."""
        return JSONResponse({"error": str(error), "code": error.code}, status_code=error.status)

    def project_root(project_id):
        """Берёт зарегистрированный корень, не путь из непроверенного ID."""
        with store.lock:
            return Path(store._project(project_id)["path"])

    @app.get("/api/projects/{project_id}/data-files")
    def project_data_files(project_id: str):
        """Только обнаружение по имени/stat для кнопки обычного проекта."""
        return {"projectId": project_id, **discover(project_root(project_id), store.data_dir)}

    @app.post("/api/data-files")
    def standalone_files(body: dict):
        """Явно выбранный каталог можно изучить до создания карты."""
        require(set(body) == {"root"} and isinstance(body["root"], str), "INVALID_ROOT", "Укажите root.")
        root = Path(body["root"]).expanduser()
        require(root.is_absolute() and root.is_dir() and not root.resolve().is_relative_to(store.data_dir), "INVALID_ROOT", "Нужен абсолютный путь к каталогу вне хранилища Atlas.")
        return discover(root.resolve(), store.data_dir)

    @app.get("/api/data-maps")
    def list_data_maps(projectId: str | None = None):
        """Список существующих карт, без автоматического построения."""
        if projectId:
            project_root(projectId)
        return data_maps.list(projectId)

    @app.post("/api/data-maps", status_code=202)
    def create_data_map(body: DataInput):
        """Только явный POST запускает анализ выбранных файлов."""
        if body.projectId:
            root = project_root(body.projectId)
            require(body.root is None or Path(body.root).resolve() == root.resolve(), "SCOPE_MISMATCH", "Корень не соответствует выбранному проекту.", 403)
        else:
            require(body.root is not None, "INVALID_ROOT", "Укажите абсолютный root или projectId.")
            root = Path(body.root).expanduser()
            require(root.is_absolute(), "INVALID_ROOT", "Нужен абсолютный root.")
        return data_maps.create(body, root)

    @app.get("/api/data-maps/{map_id}")
    def data_overview(map_id: str):
        """Показывает актуальность; даже stale обзор не запускает пересборку."""
        return data_maps.overview(map_id)

    @app.post("/api/data-maps/{map_id}/refresh", status_code=202)
    def refresh_data_map(map_id: str, body: dict):
        """Явно проверяет хеши и обновляет карту только при изменении/force."""
        require(set(body) <= {"force"} and type(body.get("force", False)) is bool, "INVALID_REQUEST", "Ожидается force: bool.")
        return data_maps.refresh(map_id, body.get("force", False))

    @app.get("/api/data-jobs/{job_id}")
    def get_data_job(job_id: str):
        """Возвращает прогресс без возобновления прерванного задания."""
        return data_maps.job(job_id)

    @app.post("/api/data-jobs/{job_id}/cancel")
    def cancel_data_job(job_id: str):
        """Останавливает импорт; опубликованные данные сохраняются."""
        return data_maps.job(job_id, cancel=True)

    @app.post("/api/data-maps/{map_id}/query/{operation}")
    def query_data_map(map_id: str, operation: str, body: DataQuery):
        """Читает опубликованный снимок и возвращает дату/свежесть без пересборки."""
        return data_maps.query(map_id, operation, body)

    @app.get("/api/data-maps/{map_id}/export")
    def export_data_map(map_id: str, snapshotId: str = Query(min_length=1, max_length=128)):
        """Graphify nodes/links содержит структуру; полные записи не экспортируются."""
        item, metadata = data_maps.snapshot(map_id, snapshotId)
        result = schema_graph(metadata)
        result["graph"].update(builtAt=metadata["builtAt"], freshness=data_maps.freshness(item))
        data_maps.snapshot(map_id, snapshotId)
        return Response(encode(result), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="data-map-{map_id}.json"'})

    @app.get("/api/data-maps/{map_id}/views/{view_id}")
    def saved_data_view(map_id: str, view_id: str):
        """Исторический вид доступен отдельно от актуального анализа."""
        return data_maps.saved_view(map_id, view_id)

    @app.get("/api/technical/storage")
    def technical_storage():
        """
        Return existing code/data map storage metadata with explicit snapshot-cleanup capability.

        Returns:
            Current catalog, logical file sizes and pending physical-quarantine status; no snapshots are cleared.
        """
        result = data_maps.technical()
        code_maps = []
        with store.lock:
            for project in store.projects.values():
                graph = project.get("_graph")
                try:
                    directories = project_directories(store.data_dir, project["id"])
                    size = sum(storage_bytes(directory) for directory in directories)
                except ValueError:
                    size = None
                code_maps.append({"id": project["id"], "title": project["name"], "status": project["status"],
                                  "builtAt": project.get("updatedAt"), "snapshotId": Path(graph).parent.name if graph else None,
                                  "sizeBytes": size, "kind": "code", "url": "/?repo=" + project["id"],
                                  "freshness": {"status": "not-checked", "reason": "Свежесть исходников карты кода здесь не проверяется."}})
        cleanup = getattr(app.state, 'map_cleanup', None)
        if cleanup is not None:
            for row in result['maps']:
                row['cleanup'] = cleanup.availability('data', row['id'])
            for row in code_maps:
                row['cleanup'] = cleanup.availability('code', row['id'])
            result['cleanupStorage'] = cleanup.storage_status()
        result["codeMaps"] = code_maps
        result["mapsPath"] = str(data_maps.data_dir / "maps")
        result["totalMapBytes"] = None
        if result["sizeBytes"] is not None and all(item["sizeBytes"] is not None for item in code_maps):
            result["totalMapBytes"] = result["sizeBytes"] + sum(item["sizeBytes"] for item in code_maps)
        return result

    @app.post("/api/technical/open-storage")
    def open_storage(body: dict):
        """Открывает только известный каталог Atlas в штатном файловом менеджере."""
        require(set(body) <= {"mapId"}, "INVALID_REQUEST", "Допустим только mapId.")
        require(body.get("mapId") is None or isinstance(body["mapId"], str), "INVALID_REQUEST", "mapId должен быть строкой.")
        path = data_maps.existing_directory(body["mapId"]) if body.get("mapId") else data_maps._path("maps")
        require(path.is_dir(), "STORAGE_MISSING", "Каталог ещё не создан.", 404)
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise DataError("OPEN_UNAVAILABLE", "Файловый менеджер недоступен; скопируйте показанный путь.", 503) from None
        return {"opened": True, "path": str(path)}
