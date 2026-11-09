"""One background worker refreshes captured health after map publication, independently of Graphify."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import RLock
from uuid import uuid4

from .code_health import ANALYZER_VERSION, enrich_code_health
from .code_health_policy import policy
from .health_snapshot_overlay import MAX_OVERLAY_BYTES, file_stamp, read_overlay, snapshot_hash
from .store import write_json
from .health_payload_codec import encode_health_payloads
from .health_snapshot_delta import VERSION as DELTA_VERSION, capture_health_baseline, health_delta


class CodeHealthRefresh:
    """Jobs score captured facts only; unknown dependencies stay unknown and source files are never executed/read."""
    def __init__(self, store, evaluate=enrich_code_health):
        """Own a serial queue and a bounded status registry; publication is guarded by the current snapshot ID."""
        self.store, self.evaluate = store, evaluate
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='atlas-health')
        self.lock, self.jobs, self.closed = RLock(), OrderedDict(), False

    def _scope(self, project_id, snapshot_id=None):
        """Reject requests for an old snapshot before scheduling or reading its derived state."""
        from .code_health_records import HealthError
        with self.store.lock:
            path = self.store._snapshot_path(project_id, '_graph')
            if snapshot_id and path.parent.name != snapshot_id:
                raise HealthError('Карта обновилась. Загрузите её перед пересчётом здоровья.', 409)
            return path

    def snapshot_published(self, project_id, snapshot_id):
        """Enqueue once after a successful map build; failure never rolls back the source map."""
        return self.request(project_id, snapshot_id, force=False)

    def request(self, project_id, snapshot_id=None, *, force=True):
        """Deduplicate an active revision and bound the queue to registered projects and 100 retained jobs."""
        from .code_health_records import HealthError
        path = self._scope(project_id, snapshot_id)
        current = self.status(project_id, path.parent.name) if not force else None
        with self.lock:
            if self.closed:
                raise HealthError('Фоновый расчёт здоровья остановлен.', 503)
            active = next((job for job in reversed(self.jobs.values()) if job['projectId'] == project_id
                           and job['snapshotId'] == path.parent.name and job['status'] in {'queued', 'running'}), None)
            if active:
                return dict(active)
            if current and current['status'] == 'completed':
                return current
            queued = sum(job['status'] in {'queued', 'running'} for job in self.jobs.values())
            if queued >= 32:
                raise HealthError('Очередь здоровья занята; повторите позже.', 429)
            job = {'id': uuid4().hex, 'projectId': project_id, 'snapshotId': path.parent.name, 'status': 'queued',
                   'policyVersion': policy()['version'], 'analyzerVersion': ANALYZER_VERSION, 'sourceMode': 'snapshot', 'createdAt': self._now()}
            self.jobs[job['id']] = job
            self._save(job, path)
            self.executor.submit(self._run, job['id'], path)
            while len(self.jobs) > 100:
                retired = next(key for key, value in self.jobs.items() if value['status'] not in {'queued', 'running'})
                self.jobs.pop(retired)
            return dict(job)

    def _now(self):
        """Use UTC timestamps consistently with immutable health assessments."""
        return datetime.now(timezone.utc).isoformat()

    def _save(self, job, path):
        """Persist state outside the original graph so it survives a normal application restart."""
        write_json(path.with_name('code-health-refresh.json'), job)

    def _set(self, job_id, path, **values):
        """Publish a small status transition; callers never retain mutable worker dictionaries."""
        with self.lock:
            job = self.jobs[job_id]
            job.update(values, updatedAt=self._now())
            self._save(job, path)

    def _run(self, job_id, path):
        """Score a pinned captured graph and publish a derivative only if that revision is still current."""
        try:
            self._set(job_id, path, status='running')
            source_stamp = file_stamp(path)
            from .snapshot_objects import snapshot_source_stamp
            if snapshot_source_stamp(path)[1] > 256 * 1024 * 1024:
                raise ValueError('Снимок превышает бюджет фоновой оценки 256 MiB.')
            source_hash = snapshot_hash(path, source_stamp)
            graph = self.store._read_graph(path)
            graph.setdefault('metadata', {})['snapshotId'] = path.parent.name
            baseline = capture_health_baseline(graph)
            result = self.evaluate(graph)
            captured_health = result['metadata']['codeHealth']
            if (captured_health.get('analyzerVersion') != self.jobs[job_id]['analyzerVersion']
                    or captured_health.get('policyVersion') != self.jobs[job_id]['policyVersion']):
                self._set(job_id, path, status='superseded')
                return
            summary = captured_health['summary']
            health_measured_at = captured_health.get('measuredAt')
            changed, removed = health_delta(result, baseline, health_measured_at)
            overlay = {'jobId': job_id, 'snapshotId': path.parent.name, 'sourceStamp': list(source_stamp),
                       'policyVersion': self.jobs[job_id]['policyVersion'],
                       'analyzerVersion': self.jobs[job_id]['analyzerVersion'], 'sourceHash': source_hash,
                       'measuredAt': self._now(),
                       'codeHealth': result['metadata']['codeHealth'],
                       'formatVersion': DELTA_VERSION, 'healthMeasuredAt': health_measured_at,
                       'nodes': changed, 'removedFields': removed}
            persisted_overlay = encode_health_payloads(overlay)
            if len(json.dumps(persisted_overlay, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > MAX_OVERLAY_BYTES:
                raise ValueError('Производная оценка превышает бюджет 64 MiB.')
            with self.store.lock:
                if (self.store._snapshot_path(self.jobs[job_id]['projectId'], '_graph') != path
                        or file_stamp(path) != source_stamp
                        or self.jobs[job_id]['policyVersion'] != policy()['version']
                        or self.jobs[job_id]['analyzerVersion'] != ANALYZER_VERSION):
                    self._set(job_id, path, status='superseded')
                    return
                write_json(path.with_name('code-health-overlay.json'), persisted_overlay)
                self.store._published_graph.pop(self.jobs[job_id]['projectId'], None)
            self._set(job_id, path, status='completed', measuredAt=overlay['measuredAt'],
                      methodCount=summary.get('methodCount'), assessedCount=summary.get('assessedCount'),
                      notApplicableCount=max(0, (summary.get('methodCount') or 0) - (summary.get('assessedCount') or 0) - (summary.get('unknownCount') or 0)),
                      unknownCount=summary.get('unknownCount'), resultStatus='partial' if summary.get('unknownCount') else 'complete')
        except Exception as error:
            with self.lock:
                job = self.jobs[job_id]
                job.update(status='failed', error=str(error)[-500:] or type(error).__name__, updatedAt=self._now())
                try:
                    self._save(job, path)
                except OSError:
                    job['statePersistenceFailed'] = True

    def status(self, project_id, snapshot_id=None):
        """Read retained state; interrupted jobs become retryable instead of appearing to run forever."""
        path = self._scope(project_id, snapshot_id)
        with self.lock:
            current = next((dict(job) for job in reversed(self.jobs.values()) if job['projectId'] == project_id
                            and job['snapshotId'] == path.parent.name), None)
        job = current
        if job is None:
            try:
                job = json.loads(path.with_name('code-health-refresh.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                job = None
        if not isinstance(job, dict) or job.get('snapshotId') != path.parent.name:
            with self.store.lock:
                pending = self.store.jobs.get(path.parent.name, {}).get('followupsPending', False)
            return {'status': 'queued' if pending else 'not-requested', 'projectId': project_id, 'snapshotId': path.parent.name}
        with self.lock:
            if job.get('status') in {'queued', 'running'} and job.get('id') not in self.jobs:
                return {**job, 'status': 'interrupted'}
        if job.get('status') == 'completed' and read_overlay(path) is None:
            return {**job, 'status': 'unavailable'}
        return job

    def close(self):
        """Stop accepting jobs and finish the bounded captured-fact queue before ProjectStore closes."""
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True)
