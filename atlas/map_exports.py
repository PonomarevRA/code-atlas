"""Короткоживущий локальный HTTP-файл для скачивания SVG встроенным браузером."""
from collections import OrderedDict
from time import monotonic
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

MAX_EXPORT_BYTES = 16_000_000


class MapExportInput(BaseModel):
    """Принимает только подготовленную SVG-карту и имя скачиваемого файла."""
    model_config = ConfigDict(extra="forbid")
    svg: str = Field(min_length=1, max_length=MAX_EXPORT_BYTES)
    filename: str = Field(min_length=1, max_length=160)


def register_map_export_routes(app):
    """Держит до двух SVG в памяти; ссылки действуют пять минут, очистка выполняется при обращении."""
    exports = OrderedDict()

    def expire():
        """Освобождает истёкшие экспорты при следующем обращении к локальному API."""
        now = monotonic()
        for key, (_, _, deadline) in list(exports.items()):
            if deadline <= now:
                del exports[key]

    @app.post("/api/map-export")
    async def prepare(payload: MapExportInput):
        """Возвращает локальную ссылку с Content-Disposition вместо ненадёжного SVG Blob-download."""
        expire()
        content = payload.svg.encode("utf-8")
        if len(content) > MAX_EXPORT_BYTES:
            raise HTTPException(413, "SVG слишком большой. Экспортируйте меньше раскрытых элементов.")
        if not payload.svg.lstrip().startswith("<svg") or "<!DOCTYPE" in payload.svg.upper():
            raise HTTPException(422, "Ожидается SVG-карта.")
        filename = payload.filename
        if not filename.endswith(".svg") or any(character in filename for character in '\\/:*?"<>|\r\n'):
            raise HTTPException(422, "Некорректное имя SVG-файла.")
        while len(exports) >= 2:
            exports.popitem(last=False)
        key = uuid4().hex
        exports[key] = (content, filename, monotonic() + 300)
        return {"url": f"/api/map-export/{key}"}

    @app.get("/api/map-export/{key}")
    async def download(key: str):
        """Отдаёт неизменённый SVG как файл; неизвестные и истёкшие ссылки отвечают 404."""
        expire()
        if key not in exports:
            raise HTTPException(404, "Экспорт истёк. Создайте изображение заново.")
        content, filename, _ = exports[key]
        disposition = f"attachment; filename=code-atlas-map.svg; filename*=UTF-8''{quote(filename, safe='')}"
        return Response(content, media_type="image/svg+xml", headers={"Content-Disposition": disposition})
