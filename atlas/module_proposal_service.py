"""Source-bound opt-in search and incremental state using existing analysis residency."""

from contextlib import contextmanager
from threading import BoundedSemaphore
import time

from .analysis_contexts import AnalysisContextError
from .mcp_response_budget import json_bytes
from .module_analysis import ModuleAnalysisError, compare_partitions, project_modules
from .module_analysis.models import digest
from .module_analysis_service import projection_summary
from .module_search import ModuleSearchError, search_partitions
from .module_search.contracts import MAX_UNITS as SEARCH_MAX_UNITS
from .module_updates import derive_delta, initial_state, update


class ModuleProposalService:
    """The host owns snapshots/admission; pure engines own objectives, never source IO."""

    def __init__(self, store, contexts, *, slots=None, clock=time.monotonic):
        """
        Bind existing source and residency owners without starting workers.

        Args:
            store: Canonical ProjectStore; proposals never publish a map.
            contexts: Existing AnalysisContexts; state handles share its quotas/expiry/close API.
            slots: Shared ModuleAnalysisService semaphore or a bounded two-request default.
            clock: Monotonic deadline clock, injectable for deterministic cancellation tests.
        """
        self.store, self.contexts, self.clock = store, contexts, clock
        self.slots = slots if slots is not None else BoundedSemaphore(2)

    @contextmanager
    def _capture(self, project_id, request):
        """
        Yield one host-captured projection while holding bounded maintenance ownership.

        Args:
            project_id: Selected existing project; never a caller graph or source path.
            request: Validated scope with exact snapshotId and optional folder/class ownership.
        Yields:
            Projection derived only from the captured store graph.
        Raises:
            ModuleAnalysisError: Admission exhausted; scope validation failures propagate.
            StoreError: Source is missing or its snapshot changed before response publication.
        """
        if not self.slots.acquire(blocking=False):
            raise ModuleAnalysisError('Module analysis is busy.', code='module_analysis_busy', status=429)
        release = None
        try:
            maintenance = getattr(self.store, 'maintenance', None)
            if maintenance is not None:
                release = maintenance.enter()
            snapshot = request['snapshotId']
            self.store.assert_snapshot(project_id, snapshot)
            _, graph = self.store.comparison_snapshot(project_id)
            self.store.assert_snapshot(project_id, snapshot)
            projection = project_modules(graph, level=request.get('level', 'folder'),
                folders=request.get('folders'), ownership=request.get('ownership'), snapshot_id=snapshot)
            yield projection
            self.store.assert_snapshot(project_id, snapshot)
        finally:
            if release is not None:
                release()
            self.slots.release()

    @staticmethod
    def _bounded(result):
        """Reject an oversized envelope without cutting a partition or hiding its limits."""
        if json_bytes(result) > 64_000:
            raise ModuleAnalysisError('Proposal response exceeds 64000 bytes; select a smaller scope.',
                                      code='module_analysis_budget', status=413)
        return result

    def search(self, project_id, request, *, cancel_event=None):
        """
        Compare finite Pareto candidates with explicit upstream baseline generation costs.

        Args:
            project_id: Host project selected by transport scope.
            request: Captured scope, currentPartition, constraints, seed and bounded search budget.
            cancel_event: Optional HTTP-disconnect event; partially evaluated candidates are not published.
        Returns:
            Read-only source-bound proposals; server deadline uses cooperative cancellation.
            No candidate is applied to source, default grouping or geometry.
        """
        with self._capture(project_id, request) as projection:
            if len(projection['units']) > SEARCH_MAX_UNITS:
                raise ModuleSearchError('Search is limited to 128 units; narrow the scope.',
                                        code='module_search_budget', status=413)
            # External baseline generation is measured separately from equal search-arm caps.
            comparison = compare_partitions(projection, algorithms=['sarf-simple-directed', 'leiden'],
                                             seed=request.get('seed', 42))
            baselines = {}
            for proposal in comparison['proposals']:
                name = 'sarf' if proposal['algorithm'] == 'sarf-simple-directed' else 'leiden'
                baselines[name] = proposal.get('partition') if proposal['status'] == 'available' else None
            deadline = self.clock() + 15.0
            current = request.get('currentPartition')
            if current is None:
                current = [[unit['id']] for unit in projection['units']]
            search = search_partitions(projection, current, constraints=request.get('constraints'),
                seed=request.get('seed', 42), evaluations_per_method=request.get('evaluationsPerMethod', 64),
                max_proposals=request.get('maxProposals', 8), baselines=baselines,
                cancelled=lambda: self.clock() >= deadline or (cancel_event is not None and cancel_event.is_set()))
            return self._bounded({'projectId': project_id, 'snapshotId': request['snapshotId'],
                'projection': projection_summary(projection), 'search': search,
                'baselineGeneration': comparison, 'readOnly': True,
                'deadlineSeconds': 15, 'deadlineScope': 'search arms; external baseline generation is separately timed'})

    @staticmethod
    def _settings(projection):
        """Bind a retained state to the actual module scope, independently of task labels."""
        return 'module-update/v1:' + digest({'level': projection['level'], 'scope': projection['scope']})

    def _retain(self, project_id, projection, state):
        """
        Publish a state reference only after existing residency actually retained it.

        Args:
            project_id: Context owner; generic close_analysis_context can release this same scope.
            projection: Actual captured old graph, never supplied by an HTTP caller.
            state: Immutable UpdateState owned by the pure engine.
        Returns:
            Opaque contextId/snapshot/settings reference; data bodies are not round-tripped to callers.
        Raises:
            AnalysisContextError: Capacity/expiry or retention budget prevents a usable handle.
        """
        settings, snapshot = self._settings(projection), projection['snapshotId']
        handle = self.contexts.open(project_id, snapshot, settings)
        identifier = handle['contextId']
        size = json_bytes(projection) + json_bytes(state.to_dict())
        try:
            if size > self.contexts.max_context_bytes:
                raise AnalysisContextError('Incremental state exceeds residency budget', 413)
            value = (projection, state)
            self.contexts.value(identifier, project_id, snapshot, settings, 'module-update-state',
                                lambda: value, lambda item: (size, len(projection['units'])))
            def absent():
                """Oversized/global-budget values may be computed but never retained by the common cache."""
                raise AnalysisContextError('Incremental state was not retained; close an unused context', 429)
            self.contexts.value(identifier, project_id, snapshot, settings, 'module-update-state',
                                absent, lambda item: (size, len(projection['units'])))
            self.store.assert_snapshot(project_id, snapshot)
            self.contexts.touch(identifier, project_id, snapshot, settings)
            return {'contextId': identifier, 'snapshotId': snapshot, 'settings': settings,
                    'idleTimeoutSeconds': handle['idleTimeoutSeconds']}
        except Exception:
            self.contexts.release(identifier, project_id, snapshot)
            raise

    def discard(self, project_id, result):
        """Release only an undelivered result's exact host context, never another project scope."""
        reference = result.get('stateRef')
        if reference is not None:
            try:
                self.contexts.release(reference['contextId'], project_id, reference['snapshotId'])
            except AnalysisContextError:
                pass

    def incremental(self, project_id, request, *, cancel_event=None):
        """
        Initialize or update from a host-retained old state and an independently captured new graph.

        Args:
            project_id: Explicit context/project owner.
            request: New scope/snapshot, optional prior stateRef and evaluation cap.
            cancel_event: Optional stop request checked before new residency is published.
        Returns:
            Detached hierarchy/delta/Q report plus a new bounded context handle. The previous
            handle stays caller-owned and can be closed with its original snapshot.
        Raises:
            AnalysisContextError: Forged, foreign, expired or evicted old state reference.
        """
        retained = None
        try:
            with self._capture(project_id, request) as projection:
                reference = request.get('stateRef')
                if reference is None:
                    state = initial_state(projection, request.get('initialPartition'),
                                          max_evaluations=request.get('maxEvaluations', 4096))
                else:
                    def absent():
                        """Never rebuild an untrusted old state from a caller's declaration."""
                        raise AnalysisContextError('Prior incremental state expired or was not retained')
                    old_projection, previous = self.contexts.value(reference['contextId'], project_id,
                        reference['snapshotId'], reference['settings'], 'module-update-state', absent,
                        lambda item: (0, 0))
                    delta = derive_delta(old_projection, projection)
                    state = update(previous, projection, delta,
                                   max_evaluations=request.get('maxEvaluations', 4096)).state
                report = {'projectId': project_id, 'snapshotId': request['snapshotId'],
                          'projection': projection_summary(projection), 'update': state.report,
                          'readOnly': True, 'algorithm': state.version}
                # Check the envelope before allocating a new handle, then include its exact bytes.
                self._bounded(report)
                if cancel_event is not None and cancel_event.is_set():
                    raise AnalysisContextError('Incremental request was cancelled before retention', 499)
                retained = self._retain(project_id, projection, state)
                return self._bounded({**report, 'stateRef': retained})
        except Exception:
            if retained is not None:
                self.contexts.release(retained['contextId'], project_id, retained['snapshotId'])
            raise
