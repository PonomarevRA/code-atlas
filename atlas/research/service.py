"""Separate bounded worker queue for explicitly activated local fact workflows."""
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError, version
import threading
from .documents import compose_document
from .evidence import EvidenceCollector
from .graphs import WORKFLOW_VERSION
from .models import ResearchError
from .storage import ResearchStore, now


class ResearchInterrupted(Exception):
    """Distinguish cooperative application shutdown from stale evidence and user cancellation."""


class ResearchContext:
    """Per-run services and cancellation checks never enter serialized LangGraph state."""
    def __init__(self, service, run):
        self.service, self.run = service, run
        self.collector = EvidenceCollector(service.projects, service.records, service.references, service.libraries, run)

    def check(self, phase):
        """Recheck activation/snapshot before every step and respect persistent cancellation."""
        if self.service.closed:
            raise ResearchInterrupted()
        if self.service.records.get(self.run['id'])['status'] == 'cancelled':
            raise ResearchError('Исследование отменено.', 409)
        self.service.workflow(self.run)
        self.service.projects.assert_snapshot(self.run['projectId'], self.run['snapshotId'])
        self.service.records.transition(self.run['id'], 'running', phase, expected={'running'})

    def publish(self, warnings):
        """Verify current source bytes before one immutable publication; a cancelled run cannot publish."""
        evidence = self.service.records.evidence(self.run['id'])
        freshness = self.collector.verify()
        if freshness['status'] == 'stale':
            raise ResearchError('Исходники изменились. Создайте новое исследование.', 409)
        if freshness['status'] == 'partial':
            warnings = [*warnings, 'Часть исходников недоступна; актуальность этих файлов не проверена.']
        self.check('Сохраняем проверенную сводку фактов')
        document = compose_document(self.run, evidence, warnings)
        document['freshness'] = {**freshness, 'checkedAt': now()}
        if len(document['markdown'].encode()) > 512_000:
            raise ResearchError('Документ превышает лимит. Уменьшите число символов.')
        self.service.records.transition(self.run['id'], 'completed', 'Готово', expected={'running'}, document=document)


class ResearchService:
    """One research worker shares the existing ProjectStore; map extraction has its own queue."""
    def __init__(self, projects, references, libraries):
        self.projects, self.references, self.libraries = projects, references, libraries
        self.records = ResearchStore(projects.data_dir)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='atlas-research')
        self.lock = threading.RLock()
        self.pending = set()
        self.closed = False

    def capabilities(self):
        """Metadata checks do not import LangGraph or start a model, network request or job."""
        versions = {}
        for package in ('langgraph', 'langgraph-checkpoint-sqlite'):
            try:
                versions[package] = version(package)
            except PackageNotFoundError:
                pass
        workflows = []
        for item in self.projects.mods.catalog()['mods']:
            if item['enabled']:
                for workflow in item['manifest'].get('workflows', []):
                    workflows.append({**workflow, 'modId': item['id'], 'fingerprint': item['fingerprint'], 'modName': item['manifest']['name']})
        return {'engineAvailable': len(versions) == 2, 'versions': versions, 'mode': 'local-facts',
                'modelConnected': False, 'network': False, 'workflows': workflows,
                'limits': {'maxNodes': 12, 'maxDepth': 3, 'maxEvidence': 24, 'maxQueuedRuns': 4}}

    def workflow(self, body):
        """Every run pins an enabled package fingerprint and its explicitly enabled dependencies."""
        item = self.projects.mods.active(body['modId'], body['fingerprint'])
        for workflow in item['manifest'].get('workflows', []):
            if workflow['id'] == body['workflowId']:
                return workflow
        raise ResearchError('Этот процесс не объявлен активным модом.', 409)

    def start(self, request):
        """Persist before accepting work; retries reuse one run, and a full queue changes no source data."""
        body = request.model_dump()
        workflow = self.workflow(body)
        self.projects.assert_snapshot(body['projectId'], body['snapshotId'])
        if not self.capabilities()['engineAvailable']:
            raise ResearchError('В этой сборке отсутствуют зависимости LangGraph.', 503)
        body.update(workflowKind=workflow['kind'], workflowVersion=WORKFLOW_VERSION)
        with self.lock:
            existing = self.records.existing(body)
            if existing:
                return existing
            if self.closed or len(self.pending) >= 4:
                raise ResearchError('Очередь исследований занята. Повторите после завершения.', 409)
            run, created = self.records.create(body)
            if created:
                self._enqueue(run['id'])
        return run

    def _enqueue(self, identifier):
        """The caller holds the service lock; one executor owns SQLite checkpoint writes."""
        self.pending.add(identifier)
        self.executor.submit(self._run, identifier)

    def _run(self, identifier):
        """Resume real checkpoints explicitly, with tracing disabled and no provider/model imports."""
        try:
            if not self.records.transition(identifier, 'running', 'Запускаем LangGraph', expected={'queued'}):
                return
            run = self.records.get(identifier)
            context = ResearchContext(self, run)
            context.check('Проверяем ранее прочитанные источники')
            if context.collector.verify()['status'] == 'stale':
                raise ResearchError('Исходники изменились после остановки. Начните новое исследование.', 409)
            from langgraph.checkpoint.sqlite import SqliteSaver
            from langsmith import tracing_context
            from .graphs import build_workflow
            with SqliteSaver.from_conn_string(str(self.records.root / 'checkpoints.sqlite')) as saver, tracing_context(enabled=False):
                graph = build_workflow(context, saver)
                config = {'configurable': {'thread_id': identifier}, 'recursion_limit': 32, 'callbacks': []}
                previous = graph.get_state(config)
                initial = None if previous.values and previous.next else {}
                for _ in graph.stream(initial, config, stream_mode='values'):
                    if self.records.get(identifier)['status'] == 'cancelled':
                        break
                if self.records.get(identifier)['status'] == 'completed':
                    saver.delete_thread(identifier)
        except ResearchInterrupted:
            self.records.transition(identifier, 'interrupted', 'Остановлено при завершении приложения', expected={'running'})
        except Exception as error:
            safe = str(error)[:500] if isinstance(error, (ResearchError, ValueError)) else 'Не удалось завершить локальное исследование. Можно продолжить или создать новое.'
            status = 'stale' if getattr(error, 'status', None) == 409 else 'failed'
            self.records.transition(identifier, status, 'Процесс остановлен', expected={'running'}, error=safe)
        finally:
            with self.lock:
                self.pending.discard(identifier)

    def cancel(self, identifier):
        """Persistent cancellation is idempotent; the current bounded read may finish before the next check."""
        self.records.get(identifier)
        self.records.transition(identifier, 'cancelled', 'Отменено', expected={'queued', 'running', 'interrupted', 'failed'})
        return self.records.get(identifier)

    def resume(self, identifier):
        """A user resumes only compatible interrupted/failed runs; never repeat completed publication."""
        with self.lock:
            run = self.records.get(identifier)
            self.workflow(run)
            if run['workflowVersion'] != WORKFLOW_VERSION or run['status'] not in {'interrupted', 'failed'}:
                raise ResearchError('Этот запуск нельзя продолжить. Создайте новый.', 409)
            if self.closed or identifier in self.pending or len(self.pending) >= 4:
                raise ResearchError('Очередь исследований занята.', 409)
            if not self.records.transition(identifier, 'queued', 'Продолжение в очереди', expected={run['status']}):
                raise ResearchError('Состояние запуска изменилось.', 409)
            self._enqueue(identifier)
        return self.records.get(identifier)

    def freshness(self, identifier):
        """Explicit recheck leaves the captured document immutable, even if its sources became stale."""
        run = self.records.get(identifier)
        return {**EvidenceCollector(self.projects, self.records, self.references, self.libraries, run).verify(), 'checkedAt': now()}

    def request_stop(self):
        """Signal shutdown without I/O; the worker or close persists resumable interruption later."""
        self.closed = True

    def close(self):
        """Shutdown does not silently resume work on the next launch."""
        with self.lock:
            self.request_stop()
            for identifier in self.pending:
                self.records.transition(identifier, 'interrupted', 'Остановлено при завершении приложения', expected={'queued', 'running'})
        self.executor.shutdown(wait=True, cancel_futures=True)
