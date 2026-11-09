"""HTTP-конфигурация и поиск внешней документации с закреплённой версией настроек."""
import asyncio

from fastapi import FastAPI

from .api_models import DocumentationURLInput, DocumentationConfigInput, DocumentationSearchInput
from .documentation_config import DocumentationConfig
from .documentation_mcp import DocumentationMCPError, discover_tools, search_documents, validate_arguments


def register_documentation_routes(app: FastAPI, documentation_config: DocumentationConfig, *,
                                  discover=discover_tools, search=search_documents,
                                  validate=validate_arguments) -> None:
    """Регистрирует транспорт MCP; два запроса ограничивают внешние процессы.

    Явные callbacks позволяют заменить адаптер в тестах без создания другого
    хранилища или зависимости этого модуля от HTTP composition root.
    """
    documentation_slots = asyncio.Semaphore(2)

    @app.get("/api/documentation-mcp/config")
    def get_documentation_config():
        """Возвращает локальную настройку и token для согласованного поиска."""
        return documentation_config.get()

    @app.post("/api/documentation-mcp/tools")
    async def documentation_tools(body: DocumentationURLInput):
        """Обнаруживает инструменты без вызова поиска или передачи исходников."""
        async with documentation_slots:
            return await discover(body.url)

    @app.post("/api/documentation-mcp/config")
    async def save_documentation_config(body: DocumentationConfigInput):
        """Сохраняет только явно выбранный допустимый инструмент текущего сервера."""
        config = documentation_config.validate(body.model_dump())
        async with documentation_slots:
            available = await discover(config["url"])
        selected = next((tool for tool in available["tools"] if tool["name"] == config["tool"]), None)
        if selected is None:
            raise DocumentationMCPError("Выберите доступный инструмент поиска документации.")
        properties = selected["inputSchema"].get("properties", {})
        argument = properties.get(config["queryArgument"])
        if not isinstance(argument, dict) or argument.get("type", "string") != "string":
            raise DocumentationMCPError("Аргумент поиска должен быть строковым полем схемы инструмента.")
        # Значение запроса появится при поиске: здесь проверяем статические поля,
        # а его pattern/minLength/enum — перед реальным tools/call.
        validation_schema = {**selected["inputSchema"], "properties": {**properties, config["queryArgument"]: {"type": "string"}}}
        validate(validation_schema, {**config["arguments"], config["queryArgument"]: ""})
        return documentation_config.save(config)

    @app.post("/api/documentation-mcp/disconnect")
    def disconnect_documentation():
        """Отключает локальную настройку и инвалидирует её текущую версию."""
        return documentation_config.disconnect()

    @app.post("/api/documentation-mcp/search")
    async def documentation_search(body: DocumentationSearchInput):
        """Выполняет выбранный поиск, отклоняя ответ от изменённой конфигурации."""
        current = documentation_config.get()
        if not current["configured"]:
            raise DocumentationMCPError("Сначала настройте локальный MCP документации.", 409)
        if body.configVersion and body.configVersion != current["configVersion"]:
            raise DocumentationMCPError("Настройка MCP изменилась. Обновите её перед поиском.", 409)
        async with documentation_slots:
            documentation_config.assert_version(current["configVersion"])
            result = await search(current["config"], body.query)
        documentation_config.assert_version(current["configVersion"])
        return {**result, "configVersion": current["configVersion"]}
