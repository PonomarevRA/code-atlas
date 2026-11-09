"""Явный поиск captured документов в отдельном namespace существующего индекса."""
from copy import deepcopy
import os
from pathlib import Path
from threading import RLock

from .documents.corpus_adapter import capture_document_corpus
from .documents.models import DocumentError, identity, require
from .semantic_embeddings import LocalEmbedding, SemanticIndexError
from .semantic_index import IndexDatabase
from .process_lock import ProcessLock
from .documents.storage import atomic_json, read_json


class DocumentSearch:
    """Разделяет document/code snapshot пары; не заменяет индекс комментариев карты кода."""

    def __init__(self, data_dir, embedder=None):
        """Создаёт лёгкие handles; ONNX, LanceDB и репозиторий здесь не открываются."""
        self.data_dir = Path(data_dir)
        cache = Path(os.environ.get('ATLAS_EMBEDDING_CACHE', str(self.data_dir / 'models')))
        self.embedder = embedder or LocalEmbedding(cache)
        self.databases, self.lock = {}, RLock()
        self.closed = False

    def _scope(self, project_id, code_snapshot_id, document_map_id, document_snapshot_id):
        """Идентичность включает проект и обе явно выбранные immutable ревизии."""
        require(isinstance(project_id, str) and len(project_id) <= 128,
                'INVALID_SCOPE', 'Нужен project ID либо пустой standalone scope.')
        for value in (code_snapshot_id, document_map_id, document_snapshot_id):
            require(isinstance(value, str) and 1 <= len(value) <= 256,
                    'INVALID_SCOPE', 'Требуются явные document/code snapshot IDs.')
        return {'projectId': project_id, 'codeSnapshotId': code_snapshot_id,
                'documentMapId': document_map_id, 'documentSnapshotId': document_snapshot_id}

    def _database(self, scope):
        """Изолированный каталог исключает перезапись code corpus или соседней document карты."""
        namespace = identity(scope)
        with self.lock:
            require(not self.closed, 'SERVICE_CLOSED', 'Служба поиска завершает работу.', 503)
            if namespace not in self.databases:
                self.databases[namespace] = IndexDatabase(self.data_dir / 'document-search' / namespace, self.embedder)
            return self.databases[namespace]

    def _require_model(self):
        """Проверяет только подготовленную локальную модель; скачивание не выполняется."""
        require(self.embedder.available(), 'MODEL_UNAVAILABLE',
                'Локальная embedding-модель не подготовлена; установите её отдельно.', 503)

    def _assert_open(self):
        """Shutdown не позволяет начатому build опубликовать новое поколение."""
        with self.lock:
            require(not self.closed, 'SERVICE_CLOSED', 'Служба поиска завершает работу.', 503)

    def _pin_manifest(self, scope):
        """Публикует durable pin до индекса: сбой может сохранить лишний снимок, но не удалить нужный."""
        path = self.data_dir / 'document-search' / 'references.json'
        with self.lock:
            try:
                with ProcessLock(path.with_name('reference-write.lock')):
                    self._write_pin(path, scope)
            except RuntimeError as error:
                raise DocumentError('SEARCH_REFERENCES_BUSY', 'Другой процесс обновляет ссылки поиска; повторите запрос.', 409) from error

    def _write_pin(self, path, scope):
        """Изменяет реестр только при удержании локального и межпроцессного writer lock."""
        value = read_json(path) if path.exists() else {'version': 1, 'references': []}
        require(isinstance(value, dict) and value.get('version') == 1 and isinstance(value.get('references'), list),
                'SEARCH_REFERENCES_INVALID', 'Реестр ссылок поиска повреждён.', 409)
        pin = {'mapId': scope['documentMapId'], 'snapshotId': scope['documentSnapshotId']}
        if pin not in value['references']:
            require(len(value['references']) < 10000, 'SEARCH_REFERENCE_LIMIT', 'Реестр ссылок поиска требует обслуживания.', 409)
            value['references'].append(pin)
            atomic_json(path, value)

    def status(self, project_id, code_snapshot_id, document_map_id, document_snapshot_id):
        """Читает manifest точной пары снимков без загрузки модели или parsers."""
        scope = self._scope(project_id, code_snapshot_id, document_map_id, document_snapshot_id)
        database = self._database(scope)
        try:
            index = database.status(project_id, code_snapshot_id)
        except SemanticIndexError as error:
            raise DocumentError('SEARCH_INDEX_INVALID', str(error), error.status) from error
        available = self.embedder.available()
        state = 'ready' if index else 'not-indexed'
        if not available:
            state = 'model-unavailable'
        return {**scope, 'status': state, 'index': index, 'modelAvailable': available,
                'model': self.embedder.metadata(), 'buildPolicy': 'explicit-only'}

    def build(self, artifact_root, manifest, graph, project_id, code_snapshot_id, *, force=False, limits=None):
        """Строит только выбранный captured corpus; caller подтверждает доступ к scopes.

        Исторические ревизии допустимы. Dictionary inputs копируются до записи; source
        files не открываются, а исходный индекс code documentation остаётся независимым.
        """
        require(type(force) is bool, 'INVALID_OPTIONS', 'force должен быть boolean.')
        manifest, graph = deepcopy(manifest), deepcopy(graph)
        scope = self._scope(project_id, code_snapshot_id, manifest.get('mapId'), manifest.get('snapshotId'))
        require(manifest.get('projectId', '') == project_id, 'PROJECT_MISMATCH',
                'Document manifest принадлежит другому проекту.', 403)
        database = self._database(scope)
        self._require_model()
        self._pin_manifest(scope)
        corpus = capture_document_corpus(artifact_root, manifest, graph, code_snapshot_id, limits)
        try:
            index = database.build(project_id, code_snapshot_id, corpus, self._assert_open, force=force)
        except SemanticIndexError as error:
            raise DocumentError('SEARCH_BUILD_FAILED', str(error), error.status) from error
        return {**scope, 'status': 'ready', 'index': index}

    def search(self, project_id, code_snapshot_id, document_map_id, document_snapshot_id, query, *, limit=8, kind='all'):
        """Ищет только опубликованный captured corpus точной пары, не выполняя ensure/build."""
        scope = self._scope(project_id, code_snapshot_id, document_map_id, document_snapshot_id)
        require(isinstance(query, str) and bool(query.strip()) and len(query) <= 500,
                'INVALID_QUERY', 'Нужен поисковый текст до 500 символов.')
        require(type(limit) is int and 1 <= limit <= 50, 'INVALID_LIMIT', 'Размер выдачи должен быть 1..50.')
        require(kind in {'all', 'comment', 'documentation'}, 'INVALID_QUERY', 'Неизвестный вид corpus chunk.')
        database = self._database(scope)
        self._require_model()
        try:
            result = database.search(project_id, code_snapshot_id, query, limit=limit, kind=kind)
        except SemanticIndexError as error:
            raise DocumentError('SEARCH_UNAVAILABLE', str(error), error.status) from error
        return {**scope, **result}

    def close(self):
        """Освобождает созданные database handles, сохраняя опубликованные поколения."""
        with self.lock:
            self.closed = True
            databases = list(self.databases.values())
            self.databases.clear()
        for database in databases:
            database.close()
