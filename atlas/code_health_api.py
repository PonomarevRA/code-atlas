"""Транспорт здоровья не владеет исходниками или очередью Graphify."""

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from .code_health_comparison import compare_assessments
from .code_health_models import HealthQuery, HealthCompareQuery, HealthPlanQuery, HealthRefreshQuery
from .code_health_planning import plan_changes
from .code_health_policy import policy
from .code_health_records import HealthError
from .code_health_record_page import health_record_page


def register_code_health_routes(app: FastAPI, service) -> None:
    """Подключает read/plan/compare к единственному ProjectStore без скрытой переиндексации."""
    @app.exception_handler(HealthError)
    async def health_error(request, error):
        return JSONResponse({"error": str(error)}, status_code=error.status)

    @app.get("/api/code-health/policy")
    def get_policy():
        """Раскрывает действующие веса, пороги и бюджеты advisory политики."""
        return policy()

    @app.post("/api/projects/{project_id}/code-health")
    def assess(project_id: str, body: HealthQuery):
        """Возвращает immutable оценку снимка или явно выбранного рабочего участка."""
        return service.assess(project_id, body)

    @app.get("/api/projects/{project_id}/code-health/refresh")
    def refresh_status(project_id: str, snapshotId: str | None = Query(default=None, max_length=128)):
        """Return asynchronous refresh state separately from whether every numeric component is known."""
        return service.refresh.status(project_id, snapshotId)

    @app.post("/api/projects/{project_id}/code-health/refresh", status_code=202)
    def refresh(project_id: str, body: HealthRefreshQuery):
        """Explicit retry uses the captured snapshot; it does not rebuild the map or scan current source."""
        return service.refresh.request(project_id, body.snapshotId)

    @app.get("/api/projects/{project_id}/code-health/report")
    def report(project_id: str, snapshotId: str | None = Query(default=None, max_length=128),
               limit: int = Query(default=40, ge=1, le=120), cursor: int = Query(default=0, ge=0),
               order: str = Query(default="health", pattern="^(health|documentation)$"),
               locale: str = Query(default="en", pattern="^(ru|en)$")):
        """Готовые факты и пагинация; запрос не читает рабочие файлы."""
        return service.report(project_id, snapshotId, limit=limit, offset=cursor, order=order, locale=locale)

    @app.get("/api/projects/{project_id}/code-health/records/{identifier}")
    def record(project_id: str, identifier: str, kind: str = Query(default="assessment", pattern="^(assessment|plan)$")):
        """Reads an immutable result without measuring again or rebuilding the graph."""
        with service.store.lock:
            service.store._project(project_id)
        result = service.records.read(project_id, identifier, kind)
        return result or {"status": "unavailable", "verdict": "UNKNOWN", "reason": "Record absent or expired"}

    @app.post("/api/projects/{project_id}/code-health/plan")
    def plan(project_id: str, body: HealthPlanQuery):
        """Оценивает предлагаемый текст или диапазоны без записи файлов проекта."""
        with service.store.lock:
            service.store._project(project_id)
        return plan_changes(service, project_id, body)

    @app.get("/api/projects/{project_id}/code-health/records/{identifier}/page")
    def record_page(project_id: str, identifier: str,
                    kind: str = Query(default="assessment", pattern="^(assessment|plan)$"),
                    offset: int = Query(default=0, ge=0, le=100000),
                    limit: int = Query(default=10, ge=1, le=20)):
        """Page retained identities and facts without re-reading changed working files."""
        from urllib.parse import quote
        with service.store.lock:
            service.store._project(project_id)
        retained = service.records.read(project_id, identifier, kind)
        url = "/api/projects/" + quote(project_id, safe="") + "/code-health/records/" + quote(identifier, safe="")
        return health_record_page(retained, identifier, kind, offset, limit, url + "?kind=" + kind)

    @app.post("/api/projects/{project_id}/code-health/compare")
    def compare(project_id: str, body: HealthCompareQuery):
        """Сравнивает фактические оценки и при необходимости проверяет диапазон плана."""
        with service.store.lock:
            service.store._project(project_id)
        before = service.records.read(project_id, body.beforeAssessmentId)
        after = service.records.read(project_id, body.afterAssessmentId)
        plan = service.records.read(project_id, body.planId, "plan") if body.planId else None
        if plan and plan["baselineAssessmentId"] != body.beforeAssessmentId:
            raise HealthError("Plan belongs to a different baseline")
        result = compare_assessments(before, after, identity_map=body.identityMap, plan=plan, locale=body.locale)
        if body.planId and plan is None:
            result["planComparisonStatus"] = "unavailable"
        return result
