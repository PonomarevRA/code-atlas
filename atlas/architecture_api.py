"""HTTP-контракт общего сервиса архитектуры; анализ не дублируется в маршрутах."""
from typing import Literal
from fastapi import Query
from pydantic import Field
from .architecture_rules import Rules, StrictModel
from .architecture_service import ArchitectureChecks


class CheckInput(StrictModel):
    """Запуск только для существующего снимка и строгого набора правил."""
    snapshotId: str = Field(min_length=1, max_length=128)
    rules: Rules


class EvidenceInput(StrictModel):
    """Выбирает индекс известного ребра, не произвольный исходный путь."""
    edgeIndex: int = Field(ge=0, le=100000)


def register_architecture_routes(app, store):
    """Подключает ephemeral runs; путь проекта/файла никогда не приходит от клиента."""
    service = ArchitectureChecks(store)
    app.state.architecture_checks = service
    route = '/api/projects/{project_id}/architecture-checks'

    @app.post(route)
    def run(project_id: str, body: CheckInput):
        return service.run(project_id, body.snapshotId, body.rules)

    @app.get(route + '/{run_id}')
    def page(project_id: str, run_id: str,
             collection: Literal['rules', 'findings'] = 'rules',
             offset: int = Query(default=0, ge=0, le=100000),
             limit: int = Query(default=20, ge=1, le=20)):
        return service.page(project_id, run_id, collection, offset, limit)

    @app.get(route + '/{run_id}/findings/{finding_id}')
    def evidence(project_id: str, run_id: str, finding_id: str,
                 offset: int = Query(default=0, ge=0, le=100000),
                 limit: int = Query(default=20, ge=1, le=20)):
        return service.evidence(project_id, run_id, finding_id, offset, limit)

    @app.post(route + '/{run_id}/findings/{finding_id}/source')
    def source(project_id: str, run_id: str, finding_id: str, body: EvidenceInput):
        return service.source(project_id, run_id, finding_id, body.edgeIndex)
