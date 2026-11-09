"""Project-scoped orchestration of pure module analysis with bounded admission."""

from threading import BoundedSemaphore

from .mcp_response_budget import json_bytes
from .module_analysis import ModuleAnalysisError, project_modules
from .module_compute import compute_module_projection


MAX_RESPONSE_BYTES = 1_000_000
MAX_SUMMARY_UNITS = 200


def projection_summary(projection: dict) -> dict:
    """
    Describe a projection without duplicating the complete raw graph in transport.

    Counts/hash describe the full computation. Samples explicitly carry
    truncation; clients can inspect the original raw IDs using existing tools.
    """
    result = {key: projection[key] for key in (
        "version", "level", "snapshotId", "sourceGraphHash", "projectionHash",
        "scope", "coverage", "counts",
    )}
    units = []
    for unit in projection["units"][:MAX_SUMMARY_UNITS]:
        item = {key: unit[key] for key in ("id", "kind", "label", "ownershipSources")}
        for field in ("sourceIds", "files"):
            item[field] = unit[field][:3]
            item[field + "Count"] = len(unit[field])
            item[field + "Truncated"] = len(unit[field]) > 3
        units.append(item)
    result["units"] = units
    result["unitsTruncated"] = len(projection["units"]) > len(units)
    result["unitTotal"] = len(projection["units"])
    return result


class ModuleAnalysisService:
    """
    Own bounded CPU admission while the store owns snapshots and maintenance.

    No result is persisted, source code is not executed, and no background work
    is started on page load. An explicit request computes read-only proposals.
    """

    def __init__(self, store, *, concurrency: int = 2, compute=None, cancellable_compute=False):
        """
        Bind source ownership and a trusted inline or generation-backed compute port.

        Args:
            store: Snapshot/maintenance owner; never passed to the compute worker.
            concurrency: Positive maximum admitted requests; overflow fails without queuing.
            compute: Optional trusted compute_module_projection-compatible callable.
                None preserves the existing inline behavior and creates no process.
            cancellable_compute: True only for a configured worker port accepting cancel_event.
        """
        self.store = store
        self.slots = BoundedSemaphore(concurrency)
        self.compute = compute if compute is not None else compute_module_projection
        self.cancellable_compute = cancellable_compute and compute is not None

    def analyze(self, project_id: str, request: dict, *, cancel_event=None) -> dict:
        """
        Analyze one exact published snapshot and recheck it before returning.

        Args:
            project_id: Host-selected project identity.
            request: Validated pinned scope and provider parameters.
            cancel_event: Optional host cancellation event, forwarded only to an enabled worker port.

        Raises:
            ModuleAnalysisError: Invalid input, exhausted admission or response budget.
            StoreError: Project/snapshot unavailable or changed during computation.
        """
        if not self.slots.acquire(blocking=False):
            raise ModuleAnalysisError("Module analysis is busy; try again later.",
                                      code="module_analysis_busy", status=429)
        release_maintenance = None
        try:
            maintenance = getattr(self.store, "maintenance", None)
            if maintenance is not None:
                release_maintenance = maintenance.enter()
            snapshot_id = request["snapshotId"]
            self.store.assert_snapshot(project_id, snapshot_id)
            _, graph = self.store.comparison_snapshot(project_id)
            self.store.assert_snapshot(project_id, snapshot_id)
            projection = project_modules(
                graph, level=request.get("level", "folder"),
                folders=request.get("folders"), ownership=request.get("ownership"),
                snapshot_id=snapshot_id,
            )
            cancellation = {'cancel_event': cancel_event} if self.cancellable_compute else {}
            computed = self.compute(projection,
                algorithms=request.get('algorithms', ['current', 'louvain', 'leiden']),
                seed=request.get('seed', 42), resolution=request.get('resolution', 1.0), **cancellation)
            comparison = computed.get('comparison') if isinstance(computed, dict) else None
            assessment = computed.get('assessment') if isinstance(computed, dict) else None
            if (not isinstance(comparison, dict) or set(computed) != {'assessment', 'comparison'}
                    or not isinstance(assessment, dict)
                    or assessment.get('projectionHash') != projection['projectionHash']
                    or assessment.get('snapshotId') != snapshot_id
                    or comparison.get('projectionHash') != projection['projectionHash']
                    or comparison.get('snapshotId') != snapshot_id):
                raise ModuleAnalysisError('Compute result does not match the captured projection.',
                                          code='module_compute_contract', status=502)
            result = {
                "schemaVersion": "module-analysis-api/v1",
                "projectId": project_id, "snapshotId": snapshot_id,
                "projection": projection_summary(projection),
                **computed,
                "readOnly": True,
            }
            if json_bytes(result) > MAX_RESPONSE_BYTES:
                raise ModuleAnalysisError("Analysis response is too large; select a smaller folder scope.",
                                          code="module_analysis_budget", status=413)
            self.store.assert_snapshot(project_id, snapshot_id)
            return result
        finally:
            if release_maintenance is not None:
                release_maintenance()
            self.slots.release()
