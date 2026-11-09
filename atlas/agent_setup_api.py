"""Read-only discovery навыков Atlas и конфигураций MCP для редакторов и агентов."""
from pathlib import PurePosixPath
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response

from .agent_setup import agent_guide, setup_payload, skill_bytes, skill_catalog, skill_zip
from .store import StoreError


def setup_port(request: Request) -> int:
    """Берёт фактический loopback-порт; заголовки X-Forwarded не меняют конфигурацию."""
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1"} or request.url.scheme != "http":
        raise StoreError("Настройка MCP доступна только через локальный HTTP Atlas.", 400)
    try:
        port = request.url.port
    except ValueError:
        raise StoreError("Некорректный локальный порт Atlas.", 400) from None
    if port is None:
        address = request.scope.get("server")
        port = address[1] if address else 80
    return port


def register_agent_setup_routes(app: FastAPI, store) -> None:
    """Регистрирует discovery и загрузки; файлы IDE и исходники проекта не изменяются."""
    @app.get("/api/agent-setup")
    def agent_setup(request: Request, projectId: str | None = Query(default=None, min_length=1, max_length=128)):
        """Выдаёт актуальные конфигурации и выбранную область MCP без подключения клиента."""
        return setup_payload(store, setup_port(request), projectId)

    @app.get("/api/agent-setup/config")
    def config(request: Request, projectId: str | None = Query(default=None, min_length=1, max_length=128),
               format: Literal["json", "toml"] = "json"):
        """Загружает JSON Cursor или TOML Codex для объединения с настройками пользователя."""
        payload = setup_payload(store, setup_port(request), projectId)
        if format == "toml":
            return Response(payload["codexConfig"], media_type="text/plain",
                            headers={"Content-Disposition": 'attachment; filename="code-atlas-mcp.toml"'})
        return JSONResponse(payload["mcpConfig"], headers={"Content-Disposition": 'attachment; filename="mcp.json"'})

    @app.get("/api/agent-setup/skills")
    def skills():
        """Перечисляет поставляемые навыки и supporting resources без сканирования проекта."""
        return skill_catalog()

    @app.get("/api/agent-setup/skill")
    def skill(name: str = "code-atlas", resource: str = "SKILL.md"):
        """Читает только разрешённый ресурс, сохраняя старую загрузку основного SKILL.md."""
        payload = skill_bytes(name, resource)
        return Response(payload, media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{PurePosixPath(resource).name}"'})

    @app.get("/api/agent-setup/skill.zip")
    def archive(name: str = "code-atlas"):
        """Загружает полную папку выбранного навыка, включая его references."""
        return Response(skill_zip(name), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}-skill.zip"'})

    @app.get("/agent-setup.md")
    def guide(request: Request, projectId: str | None = Query(default=None, min_length=1, max_length=128)):
        """Текстовая инструкция доступна агенту без JavaScript и без установленного MCP."""
        payload = setup_payload(store, setup_port(request), projectId)
        return Response(agent_guide(payload), media_type="text/markdown")
