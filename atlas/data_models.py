"""Контракты самостоятельных карт данных; чтение никогда не запускает импорт."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

MAX_INPUT_BYTES = 1_000_000_000
MAX_RECORD_BYTES = 1024 * 1024
MAX_FEATURE_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 64_000
ANALYSIS_VERSION = "data-maps-v1"


class DataError(Exception):
    """Ожидаемая ошибка с устойчивым кодом и безопасным сообщением."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


class DataInput(BaseModel):
    """Явный выбор файлов; projectId ограничивает корень зарегистрированным проектом."""

    model_config = ConfigDict(extra="forbid")
    root: str | None = Field(default=None, max_length=4096)
    projectId: str | None = Field(default=None, max_length=128)
    title: str = Field(default="Карта данных", min_length=1, max_length=160)
    files: list[str] = Field(min_length=1, max_length=1000)
    encoding: Literal["utf-8-sig", "cp1251"] = "utf-8-sig"
    delimiter: Literal["auto", ",", ";", "\t"] = "auto"
    indexFields: list[str] = Field(default_factory=list, max_length=64)
    relations: list[dict] = Field(default_factory=list, max_length=100)


class DataQuery(BaseModel):
    """Ограниченный запрос в закреплённом опубликованном снимке."""

    model_config = ConfigDict(extra="forbid")
    snapshotId: str = Field(min_length=1, max_length=128)
    collectionId: str | None = Field(default=None, max_length=256)
    recordId: str | None = Field(default=None, max_length=256)
    scenarioId: str | None = Field(default=None, max_length=256)
    exampleId: str | None = Field(default=None, max_length=256)
    nodeId: str | None = Field(default=None, max_length=256)
    keyId: str | None = Field(default=None, max_length=256)
    valueRecordId: str | None = Field(default=None, max_length=256)
    query: str = Field(default="", max_length=500)
    tag: str = Field(default="", max_length=200)
    filters: list[dict] = Field(default_factory=list, max_length=8)
    fields: list[str] = Field(default_factory=list, max_length=100)
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=20, ge=1, le=50, strict=True)
    maxDepth: int = Field(default=2, ge=0, le=3, strict=True)
    maxNodes: int = Field(default=60, ge=1, le=120, strict=True)
    relationIds: list[str] = Field(default_factory=list, max_length=100)
    title: str = Field(default="Подграф данных", min_length=1, max_length=160)


def require(condition: bool, code: str, message: str, status: int = 400) -> None:
    """Останавливает запрос до чтения данных при нарушении контракта."""
    if not condition:
        raise DataError(code, message, status)
