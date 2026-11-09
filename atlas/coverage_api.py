"""Транспорт ограниченного анализа тестов; исходники и опубликованный граф не меняются."""

from threading import BoundedSemaphore
from typing import Annotated

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .store import ProjectStore, StoreError
from .test_coverage import TestCoverageError, build_test_coverage


class CoverageInput(BaseModel):
    """Ревизия карты, необязательный корень вызовов и пути отчётов внутри репозитория."""

    snapshotId: str = Field(min_length=1, max_length=128)
    nodeId: str | None = Field(default=None, min_length=1, max_length=1024)
    reportPaths: list[Annotated[str, Field(min_length=1, max_length=1024)]] = Field(
        default_factory=list, max_length=8,
    )
    model_config = {"extra": "forbid"}


def register_coverage_routes(app: FastAPI, store: ProjectStore) -> None:
    """Регистрирует read-only анализ с проверкой снимка до и после работы.

    Два параллельных анализа не занимают всю очередь HTTP рабочих потоков.
    Повторный анализ читает текущие файлы, поэтому не использует кэш старого отчёта.
    """
    slots = BoundedSemaphore(2)

    @app.post("/api/projects/{project_id}/test-coverage")
    def test_coverage(project_id: str, body: CoverageInput) -> dict:
        """Возвращает отдельные факты условий, связей с тестами и данных Cobertura."""
        if not slots.acquire(blocking=False):
            raise StoreError("Два анализа тестов уже выполняются. Повторите запрос позже.", 429)
        try:
            project_path, graph = store.comparison_snapshot(project_id)
            if graph["metadata"]["snapshotId"] != body.snapshotId:
                raise StoreError("Карта обновилась. Откройте покрытие для актуального снимка.", 409)
            try:
                result = build_test_coverage(project_path, graph, body.snapshotId,
                                             body.nodeId, body.reportPaths)
            except TestCoverageError as error:
                raise StoreError(str(error), error.status) from error
            store.assert_snapshot(project_id, body.snapshotId)
            return {**result, "projectId": project_id}
        finally:
            slots.release()
