"""Read-only import boundary: report declarations never become execution proof."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .debug_feedback import FeedbackError, build_feedback_package, parse_junit_report


class DebugFeedbackInput(BaseModel):
    """Accept reported identities, never caller-provided captured source facts."""

    model_config = ConfigDict(extra="forbid")
    snapshotId: str = Field(min_length=1, max_length=128)
    reportId: str = Field(min_length=1, max_length=128)
    junitXml: str = Field(min_length=1, max_length=500000)
    reportedSnapshotId: str | None = Field(default=None, min_length=1, max_length=128)
    reportedBuildId: str | None = Field(default=None, min_length=1, max_length=128)


def _captured_hash(node: dict) -> str | None:
    """Use a captured source hash without substituting a path or snapshot label."""
    complexity = node.get("complexity")
    if not isinstance(complexity, dict):
        complexity = {}
    for value in (node.get("sourceByteHash"), node.get("sourceHash"),
                  complexity.get("sourceHash")):
        if isinstance(value, str) and value:
            return value
    return None


def captured_source_facts(graph: dict, report, snapshot_id: str) -> list[dict]:
    """
    Select only potentially relevant captured nodes; no source path is opened.

    A matching label is a static candidate. The package owns ambiguity checks,
    source-hash requirements and the distinction from reported execution.
    """
    nodes = graph.get("nodes", [])
    if len(nodes) > 100000:
        raise FeedbackError("source_limit", "Source graph exceeds feedback budget", 413)
    files = {case.file for case in report.cases if case.file is not None}
    names = {case.name for case in report.cases if case.file is None}
    facts = []
    for node in nodes:
        filename = node.get("file") or node.get("source_file")
        name = node.get("label")
        if filename not in files and name not in names:
            continue
        item = {
            "id": node.get("id"), "file": filename, "line": node.get("line"),
            "name": name, "sourceHash": _captured_hash(node), "snapshotId": snapshot_id,
        }
        member = node.get("memberFacts")
        if not isinstance(member, dict):
            member = {}
        if isinstance(member.get("className"), str):
            item["className"] = member["className"]
        facts.append(item)
        if len(facts) > 20000:
            raise FeedbackError("source_limit", "Matching captured source scope is too large", 413)
    return facts


def register_debug_feedback_routes(app: FastAPI, store) -> None:
    """Register an explicit bounded import without persistence or test execution."""
    @app.exception_handler(FeedbackError)
    async def feedback_error(request, error):
        """Keep stable failure categories without reflecting XML or stack traces."""
        return JSONResponse({"error": str(error), "code": error.code}, status_code=error.status)

    @app.post("/api/projects/{project_id}/debug-feedback")
    def prepare_feedback(project_id: str, body: DebugFeedbackInput):
        """Join one immutable report to an exact captured snapshot, never a caller graph."""
        store.assert_snapshot(project_id, body.snapshotId)
        report = parse_junit_report(
            body.junitXml.encode("utf-8"), report_id=body.reportId,
            snapshot_id=body.reportedSnapshotId, build_id=body.reportedBuildId,
        )
        _, graph = store.comparison_snapshot(project_id)
        store.assert_snapshot(project_id, body.snapshotId)
        facts = captured_source_facts(graph, report, body.snapshotId)
        build_id = (graph.get("metadata") or {}).get("buildId")
        if not isinstance(build_id, str) or not build_id:
            build_id = None
        result = build_feedback_package(report, body.snapshotId, facts, build_id=build_id)
        store.assert_snapshot(project_id, body.snapshotId)
        return {"projectId": project_id, **result}
