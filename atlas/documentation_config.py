"""Атомарная локальная настройка выбранного инструмента документации."""

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
import uuid

from .documentation_mcp import DocumentationMCPError, validate_url


class DocumentationConfig:
    """Хранит конфигурацию и сравнивает версии без сохранения поисковых запросов."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "documentation-mcp.json"
        self.lock = RLock()

    def get(self) -> dict:
        """Читает текущую конфигурацию и её детерминированную версию."""
        with self.lock:
            try:
                config = json.loads(self.path.read_text()) if self.path.exists() else None
                if config is not None:
                    config = self.validate(config)
            except (OSError, ValueError, TypeError):
                raise DocumentationMCPError("Не удалось прочитать настройку MCP документации.", 500) from None
            version = hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            return {"configured": config is not None, "config": config, "configVersion": version}

    @staticmethod
    def validate(config: dict) -> dict:
        """Проверяет ограниченный JSON без authheaders или произвольных полей транспорта."""
        if not isinstance(config, dict) or set(config) != {"url", "tool", "queryArgument", "arguments"}:
            raise DocumentationMCPError("Проверьте поля настройки MCP.")
        for key in ("tool", "queryArgument"):
            if not isinstance(config[key], str) or not 1 <= len(config[key]) <= 200 or any(ord(c) < 32 for c in config[key]):
                raise DocumentationMCPError("Укажите инструмент и имя аргумента поиска.")
        try:
            valid_arguments = isinstance(config["arguments"], dict) and len(json.dumps(config["arguments"], allow_nan=False)) <= 8000
        except (ValueError, TypeError):
            valid_arguments = False
        if not valid_arguments:
            raise DocumentationMCPError("Статические аргументы должны быть JSON-объектом до 8000 символов.")
        return {**config, "url": validate_url(config["url"])}

    def save(self, config: dict) -> dict:
        """Атомарно заменяет файл, сохраняя старую настройку при ошибке записи."""
        config = self.validate(config)
        with self.lock:
            temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, self.path)
            except OSError:
                raise DocumentationMCPError("Не удалось сохранить настройку MCP.", 500) from None
            finally:
                temporary.unlink(missing_ok=True)
            return self.get()

    def disconnect(self) -> dict:
        """Удаляет настройку и тем самым делает незавершённые поиски устаревшими."""
        with self.lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                raise DocumentationMCPError("Не удалось отключить MCP документации.", 500) from None
            return self.get()

    def assert_version(self, version: str) -> None:
        """Отклоняет ответ, если конфигурация изменилась во время поиска."""
        if self.get()["configVersion"] != version:
            raise DocumentationMCPError("Настройка MCP изменилась. Повторите поиск.", 409)
