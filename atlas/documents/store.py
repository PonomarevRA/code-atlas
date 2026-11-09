"""Явные задания импорта, content-cache и исторические неизменяемые bindings."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
from threading import Event, RLock
import tempfile
from uuid import uuid4

from .capture import capture, freshness
from .graph_projection import graph as graph_query
from .models import DocumentError, MAX_INPUT_BYTES, SCHEMA_VERSION, identity, require
from .leases import leased, storage_lease
from .retention import collect
from .queries import nodes_page, read_content, table_page
from .storage import atomic_json, read_json, write_revision
from .worker import run_provider


def now():
    """Возвращает однозначный UTC timestamp для manifest и jobs."""
    return datetime.now(timezone.utc).isoformat()


def resolve_default(path, **options):
    """Импорт фабрики ленивый: открытие хранилища не требует форматных зависимостей."""
    from .providers import resolve_provider
    return resolve_provider(path, **options)


class DocumentStore:
    """Хранилище одного project/root scope; чтение никогда не запускает parser."""

    def __init__(self, storage_root, project_root, project_id='', provider_resolver=None, prepublish=None, auto_retention=False):
        """Поднимает каталог и помечает оборванные jobs, сохраняя опубликованные снимки."""
        self.project_root = Path(project_root).resolve()
        self.project_id = project_id
        self.root = Path(storage_root).resolve() / 'document-maps' / identity(str(self.project_root), project_id)[:24]
        require(not self.project_root.is_relative_to(self.root), 'INVALID_ROOT', 'Корень не должен находиться внутри хранилища документов.')
        self.catalog_path = self.root / 'catalog.json'
        self.jobs_path = self.root / 'jobs.json'
        self.lock = RLock()
        self.resolver = provider_resolver or resolve_default
        self.prepublish = prepublish
        self.auto_retention = auto_retention
        self.catalog = read_json(self.catalog_path) if self.catalog_path.exists() else {}
        self.jobs = read_json(self.jobs_path) if self.jobs_path.exists() else {}
        self.events, self.futures, self.flights = {}, {}, {}
        recovered_jobs = False
        for job in self.jobs.values():
            if job['status'] in {'queued', 'running'}:
                item = self.catalog.get(job['mapId'], {})
                if item.get('status') == 'ready' and item.get('jobId') == job['id'] and item.get('snapshotId'):
                    job.update(status='completed', snapshotId=item['snapshotId'], recoveredFromPublishedManifest=True)
                else:
                    job.update(status='interrupted', errorCode='INTERRUPTED', error='Сервис перезапущен; запустите ensure явно.')
                recovered_jobs = True
        if recovered_jobs:
            atomic_json(self.jobs_path, self.jobs)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='atlas-document-job')
        self.parsers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='atlas-document-process')
        recovered = False
        for item in self.catalog.values():
            if item.get('status') in {'queued', 'running'}:
                item.update(status='interrupted', error='Импорт был прерван; запустите ensure явно.')
                recovered = True
        if recovered:
            atomic_json(self.catalog_path, self.catalog)

    def _map(self, map_id):
        """Доступен только безопасный id зарегистрированной карты текущего scope."""
        require(isinstance(map_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', map_id), 'INVALID_MAP', 'Неверный идентификатор карты.')
        require(map_id in self.catalog, 'MAP_MISSING', 'Карта документов не найдена.', 404)
        return self.catalog[map_id]

    @leased
    def maps(self):
        """Перечисляет каталог без сканирования пользовательского корня."""
        with self.lock:
            return {'maps': deepcopy(list(self.catalog.values()))}

    def list(self, project_id=None):
        """Совместимая обёртка каталога в уже закреплённом project scope."""
        return self.maps() if project_id in (None, self.project_id) else {'maps': []}

    @leased
    def ensure(self, map_id, sources, options=None, strategy=None, context=None, force=False, allow_partial=False):
        """Принимает явное задание; cache проверяется по захваченным байтам и runtime."""
        require(isinstance(map_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', map_id), 'INVALID_MAP', 'Неверный идентификатор карты.')
        require(isinstance(sources, list) and 1 <= len(sources) <= 100 and all(isinstance(item, str) for item in sources), 'INPUT_LIMIT', 'Выберите 1..100 относительных файлов.')
        require(len(set(sources)) == len(sources), 'DUPLICATE_FILES', 'Исходные файлы повторяются.')
        require(options is None or isinstance(options, dict), 'INVALID_OPTIONS', 'Настройки должны быть JSON-объектом.')
        require(context is None or isinstance(context, dict), 'INVALID_CONTEXT', 'Контекст должен быть JSON-объектом.')
        options, context = deepcopy(options or {}), deepcopy(context or {})
        timeout = options.get('workerTimeoutSeconds', 30)
        require(type(timeout) in (int, float) and 0 < timeout <= 300, 'INVALID_TIMEOUT', 'Время парсера должно быть 0..300 секунд.')
        memory_bytes = options.get('workerMemoryBytes', 512 * 1024 * 1024)
        require(type(memory_bytes) is int and 16 * 1024 * 1024 <= memory_bytes <= 2 * 1024 * 1024 * 1024, 'INVALID_MEMORY_LIMIT', 'Наблюдаемый предел RSS должен быть 16 MiB..2 GiB.')
        identity(options, context)
        with self.lock:
            job_id = uuid4().hex
            configuration = {'sources': sorted(sources), 'options': options, 'strategy': strategy, 'context': context, 'allow_partial': allow_partial}
            item = self.catalog.setdefault(map_id, {'id': map_id, 'projectId': self.project_id, 'root': str(self.project_root), 'snapshotId': None})
            # The latest explicit request owns publication; old jobs can finish only as superseded.
            item.update(status='queued', jobId=job_id, configuration=configuration, error=None)
            job = {'id': job_id, 'mapId': map_id, 'status': 'queued', 'requestedAt': now(), 'force': bool(force)}
            self.jobs[job_id], self.events[job_id] = job, Event()
            atomic_json(self.catalog_path, self.catalog)
            atomic_json(self.jobs_path, self.jobs)
            self.futures[job_id] = self.executor.submit(self._build, job_id, configuration, force)
            return deepcopy(job)

    def create(self, map_id, sources, **options):
        """Создание карты является тем же явным запросом импорта."""
        return self.ensure(map_id, sources, **options)

    def refresh(self, map_id, force=False):
        """Повторяет сохранённый явный выбор; не обнаруживает дополнительные файлы."""
        with self.lock:
            configuration = deepcopy(self._map(map_id)['configuration'])
        return self.ensure(map_id, force=force, **configuration)

    def job(self, job_id, cancel=False):
        """Возвращает состояние известного задания; отмена не затрагивает соседние jobs."""
        if cancel:
            return self.cancel(job_id)
        with self.lock:
            require(job_id in self.jobs, 'JOB_MISSING', 'Задание не найдено.', 404)
            return deepcopy(self.jobs[job_id])

    @leased
    def cancel(self, job_id):
        """Отсоединяет одного потребителя shared parse и сохраняет старый snapshot."""
        with self.lock:
            require(job_id in self.jobs, 'JOB_MISSING', 'Задание не найдено.', 404)
            job = self.jobs[job_id]
            if job['status'] in {'queued', 'running'}:
                self.events[job_id].set()
                job['status'] = 'cancelled'
                for flight in self.flights.values():
                    flight['consumers'].discard(job_id)
                item = self.catalog[job['mapId']]
                if item.get('jobId') == job_id:
                    item['status'] = 'ready' if item.get('snapshotId') else 'cancelled'
                    atomic_json(self.catalog_path, self.catalog)
            atomic_json(self.jobs_path, self.jobs)
            return deepcopy(job)

    def wait(self, job_id, timeout=None):
        """Ожидает только выбранное задание; timeout не отменяет его автоматически."""
        self.job(job_id)
        if job_id not in self.futures:
            return self.job(job_id)
        try:
            self.futures[job_id].result(timeout=timeout)
        except TimeoutError:
            pass
        return self.job(job_id)

    def _check_cancel(self, job_id):
        """Отмена проверяется перед публикацией и при ожидании shared parse."""
        require(not self.events[job_id].is_set(), 'CANCELLED', 'Задание отменено.', 409)

    def _extract(self, key, provider, captured, options, context, metadata, job_id):
        """Один parse на content-key; потребители jobs владеют независимой отменой."""
        directory = self.root / 'revisions' / key
        with self.lock:
            if (directory / 'metadata.json').exists():
                return read_json(directory / 'metadata.json'), True
            flight = self.flights.get(key)
            if flight is None:
                # A private capture outlives the first consumer's staging directory.
                private = Path(tempfile.mkdtemp(prefix='atlas-document-flight-'))
                source = private / captured.name
                shutil.copyfile(captured, source)
                source.chmod(0o444)
                flight = {'consumers': {job_id}, 'directory': private}
                self.flights[key] = flight
                flight['future'] = self.parsers.submit(self._parse_revision, key, flight, provider, source, options, context, metadata)
                flight['future'].add_done_callback(lambda future: self._release_flight(key, flight))
            else:
                flight['consumers'].add(job_id)
        try:
            while True:
                self._check_cancel(job_id)
                try:
                    flight['future'].result(timeout=0.05)
                    return read_json(directory / 'metadata.json'), False
                except TimeoutError:
                    continue
        finally:
            with self.lock:
                flight['consumers'].discard(job_id)
                if flight['future'].done() and self.flights.get(key) is flight:
                    del self.flights[key]

    def _release_flight(self, key, flight):
        """Удаляет завершённую task без consumers, чтобы failed cache можно было повторить."""
        with self.lock:
            if not flight['consumers'] and self.flights.get(key) is flight:
                del self.flights[key]

    @leased
    def _parse_revision(self, key, flight, provider, source, options, context, metadata):
        """Процесс извлекает immutable input, затем индекс публикуется атомарно."""
        def abandoned():
            """Процесс нужен, пока хотя бы одно задание сохраняет свой consumer."""
            with self.lock:
                return not flight['consumers']
        try:
            tree = run_provider(provider, source, options, context, abandoned, timeout=options.get('workerTimeoutSeconds', 30), memory_bytes=options.get('workerMemoryBytes', 512 * 1024 * 1024))
            require(not abandoned(), 'CANCELLED', 'Все потребители отменены.', 409)
            write_revision(self.root / 'revisions' / key, key, tree, metadata)
        finally:
            shutil.rmtree(flight['directory'], ignore_errors=True)

    def _build_document(self, job_id, configuration, filename, target, progress, force):
        """Захватывает файл один раз; общий byte budget учитывает и будущие partial failures."""
        require(not (self.project_root / filename).resolve().is_relative_to(self.root), 'INVALID_PATH', 'Хранилище нельзя импортировать как документ.')
        remaining_bytes = 250 * 1024 * 1024 - progress['capturedBytes']
        captured = capture(self.project_root, filename, target, max_bytes=min(MAX_INPUT_BYTES, remaining_bytes))
        progress['capturedBytes'] += captured['size']
        require(progress['capturedBytes'] <= 250 * 1024 * 1024, 'MAP_INPUT_LIMIT', 'Карта превышает 250 MiB.')
        options, context = configuration['options'], configuration['context']
        provider = self.resolver(Path(filename), strategy=configuration['strategy'], options=options, context=context)
        runtime = getattr(provider, 'runtime_fingerprint', getattr(provider, 'version', 'unknown'))
        parse_context = {**context, 'sourcePath': filename}
        cache_context = getattr(provider, 'cache_context', None)
        context_key = cache_context(Path(filename), options, parse_context) if cache_context else context
        key = identity(SCHEMA_VERSION, captured['sourceHash'], provider.id, provider.version, runtime, options, context_key, job_id if force else None)
        metadata = {
            **captured, 'provider': provider.id, 'providerVersion': provider.version,
            'runtimeFingerprint': runtime, 'schemaVersion': SCHEMA_VERSION,
            'optionsHash': identity(options), 'contextHash': identity(context_key), 'capturedAt': now(),
        }
        metadata['blobHash'] = captured['sourceHash']
        revision, reused = self._extract(key, provider, target, options, parse_context, metadata, job_id)
        # Failed/unsupported redacted-only profiles must never persist the raw capture.
        blob = self.root / 'blobs' / captured['sourceHash']
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            temporary_blob = blob.with_name(blob.name + '.' + uuid4().hex + '.tmp')
            shutil.copyfile(target, temporary_blob)
            temporary_blob.chmod(0o444)
            os.replace(temporary_blob, blob)
        progress['cacheHits'] += int(reused)
        binding = {
            **captured, 'documentId': identity(str(self.project_root), self.project_id, filename),
            'revisionId': key, 'blobHash': captured['sourceHash'],
            'coverage': revision['coverage'], 'warnings': revision['warnings'],
            'provider': provider.id, 'providerVersion': revision['providerVersion'],
            'runtimeFingerprint': revision['runtimeFingerprint'],
            'optionsHash': revision['optionsHash'], 'contextHash': revision['contextHash'],
        }
        return binding

    def _publish_build(self, job_id, configuration, documents, failures, progress):
        """Проверяет отмену/владение, затем публикует manifest → catalog → terminal job."""
        map_id = self.jobs[job_id]['mapId']
        self._check_cancel(job_id)
        snapshot_id = identity(SCHEMA_VERSION, map_id, documents, failures)
        incomplete = bool(failures) or any(item['coverage']['status'] != 'complete' for item in documents)
        manifest = {
            'snapshotId': snapshot_id, 'mapId': map_id, 'projectId': self.project_id,
            'schemaVersion': SCHEMA_VERSION, 'documents': documents, 'failures': failures,
            'coverage': {'status': 'partial' if incomplete else 'complete'}, 'createdAt': now(),
        }
        with self.lock:
            self._check_cancel(job_id)
            item = self.catalog[map_id]
            require(item.get('jobId') == job_id, 'SUPERSEDED', 'Принято более новое задание.', 409)
            if self.prepublish:
                self.prepublish(deepcopy(configuration))
            manifest_path = self.root / 'maps' / map_id / 'snapshots' / (snapshot_id + '.json')
            if not manifest_path.exists():
                atomic_json(manifest_path, manifest)
            item.update(snapshotId=snapshot_id, status='ready', builtAt=now(), error=None)
            atomic_json(self.catalog_path, self.catalog)
            self.jobs[job_id].update(status='completed', snapshotId=snapshot_id, cacheHits=progress['cacheHits'], capturedBytes=progress['capturedBytes'], failures=failures, completedAt=now())
            atomic_json(self.jobs_path, self.jobs)

    def _fail_build(self, job_id, error):
        """Фиксирует terminal error; опубликованный snapshot и чужой job остаются доступны."""
        map_id = self.jobs[job_id]['mapId']
        with self.lock:
            code = getattr(error, 'code', 'IMPORT_FAILED')
            status = 'cancelled' if code == 'CANCELLED' else 'superseded' if code == 'SUPERSEDED' else 'failed'
            self.jobs[job_id].update(status=status, error=str(error)[:2000], errorCode=code, completedAt=now())
            atomic_json(self.jobs_path, self.jobs)
            item = self.catalog[map_id]
            if item.get('jobId') == job_id:
                item.update(status='ready' if item.get('snapshotId') else status, error=str(error)[:2000])
                atomic_json(self.catalog_path, self.catalog)


    def _build(self, job_id, configuration, force):
        """Защищает весь writer lifetime; после публикации best-effort запускает bounded GC."""
        try:
            with storage_lease(self.root):
                self._build_leased(job_id, configuration, force)
        except DocumentError as error:
            self._fail_build(job_id, error)
        if self.auto_retention and self.jobs[job_id]['status'] == 'completed':
            try:
                report = self.retention(dry_run=False)
                with self.lock:
                    summary_fields = ('keepLast', 'pinnedSnapshots', 'snapshotCount', 'revisionCount', 'blobCount', 'reclaimedBytes')
                    self.jobs[job_id]['retention'] = {key: report[key] for key in summary_fields}
                    atomic_json(self.jobs_path, self.jobs)
            except (DocumentError, OSError, ValueError) as error:
                # Busy readers or unknown references postpone cleanup, not a valid publication.
                with self.lock:
                    self.jobs[job_id]['retentionWarning'] = getattr(error, 'code', 'RETENTION_FAILED')
                    atomic_json(self.jobs_path, self.jobs)

    def _build_leased(self, job_id, configuration, force):
        """Оркестрирует per-file импорт; staging очищается до публикации immutable manifest."""
        try:
            with self.lock:
                self._check_cancel(job_id)
                self.jobs[job_id]['status'] = 'running'
                atomic_json(self.jobs_path, self.jobs)
            documents, failures = [], []
            progress = {'cacheHits': 0, 'capturedBytes': 0}
            with tempfile.TemporaryDirectory(prefix='atlas-document-capture-') as directory:
                for index, filename in enumerate(configuration['sources']):
                    self._check_cancel(job_id)
                    try:
                        target = Path(directory) / str(index) / Path(filename).name
                        binding = self._build_document(job_id, configuration, filename, target, progress, force)
                        documents.append(binding)
                    except Exception as error:
                        if isinstance(error, DocumentError) and error.code == 'CANCELLED':
                            raise
                        if not configuration['allow_partial']:
                            raise
                        failures.append({'path': filename, 'code': getattr(error, 'code', 'IMPORT_FAILED'), 'message': str(error)[:2000]})
            self._publish_build(job_id, configuration, documents, failures, progress)
        except Exception as error:
            self._fail_build(job_id, error)

    @leased
    def snapshot(self, map_id, snapshot_id=None):
        """Читает текущий или исторический manifest строго внутри map/snapshot scope."""
        with self.lock:
            if self.catalog_path.exists():
                self.catalog = read_json(self.catalog_path)
            item = self._map(map_id)
            snapshot_id = snapshot_id or item.get('snapshotId')
        require(isinstance(snapshot_id, str) and re.fullmatch(r'[a-f0-9]{64}', snapshot_id), 'NOT_READY', 'Опубликованный снимок отсутствует.', 409)
        path = self.root / 'maps' / map_id / 'snapshots' / (snapshot_id + '.json')
        require(path.is_file(), 'SNAPSHOT_MISSING', 'Снимок не принадлежит выбранной карте.', 404)
        return read_json(path)

    @leased
    def freshness(self, map_id, snapshot_id=None):
        """Проверяет только stat исходников, не пересобирая и не меняя immutable snapshot."""
        return freshness(self.project_root, self.snapshot(map_id, snapshot_id))

    @leased
    def tree(self, map_id, snapshot_id=None, **query):
        """Читает только индекс выбранного снимка."""
        return nodes_page(self.root, self.snapshot(map_id, snapshot_id), 'tree', **query)

    @leased
    def search(self, map_id, query='', snapshot_id=None, **options):
        """Ищет сохранённый текст без чтения исходных файлов."""
        return nodes_page(self.root, self.snapshot(map_id, snapshot_id), 'search', query=query, **options)

    @leased
    def node(self, map_id, node_id, snapshot_id=None, **query):
        """Возвращает bounded content и provenance выбранного узла."""
        result = nodes_page(self.root, self.snapshot(map_id, snapshot_id), 'node', node_id=node_id, **query)
        require(bool(result['items']), 'NODE_MISSING', 'Узел не найден.', 404)
        return result['items'][0]

    @leased
    def read_content(self, map_id, node_id, snapshot_id=None, offset=0, limit=4000, document_id=None):
        """Возвращает следующее окно сохранённого текста без чтения blob или оригинала."""
        return read_content(self.root, self.snapshot(map_id, snapshot_id), node_id, offset=offset, limit=limit, document_id=document_id)

    @leased
    def table(self, map_id, node_id, snapshot_id=None, **query):
        """Выдаёт ограниченное окно сохранённых sparse cells."""
        return table_page(self.root, self.snapshot(map_id, snapshot_id), node_id, **query)

    @leased
    def graph(self, map_id, snapshot_id=None, **query):
        """Строит bounded graph из сохранённого дерева."""
        return graph_query(self.root, self.snapshot(map_id, snapshot_id), **query)

    @contextmanager
    def snapshot_lease(self, map_id, snapshot_id=None):
        """Закрепляет manifest и все его artifacts на время внешнего corpus/view чтения."""
        with storage_lease(self.root):
            yield self.snapshot(map_id, snapshot_id)

    def retention(self, keep_last=3, dry_run=True):
        """Хранит минимум три снимка, durable refs и active readers во всех процессах."""
        return collect(self.root, keep_last=keep_last, dry_run=dry_run)

    def close(self):
        """Отменяет незавершённые jobs и дожидается остановки дочерних процессов."""
        for job_id in list(self.jobs):
            if self.jobs[job_id]['status'] in {'queued', 'running'}:
                self.cancel(job_id)
        self.executor.shutdown(wait=True)
        self.parsers.shutdown(wait=True)
