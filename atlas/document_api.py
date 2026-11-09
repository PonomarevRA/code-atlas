"""Строгие локальные HTTP-контракты документов; кодовые карты не перестраиваются."""
from pathlib import Path

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .documents.models import DocumentError, require
from .documents.providers import PROVIDERS, available_providers
from .project_file_inventory import project_file_inventory
from .document_catalog import catalog_page


class DocumentInput(BaseModel):
    """Явный выбор пользователя; никакие пути/команды из документа не исполняются."""
    projectId: str | None = Field(default=None, max_length=128)
    root: str | None = Field(default=None, max_length=4096)
    mapId: str | None = Field(default=None, max_length=128)
    title: str = Field(default='Документы', max_length=160)
    files: list[str] | None = Field(default=None, max_length=100)
    strategy: str | None = Field(default=None, max_length=80)
    options: dict | None = None
    context: dict | None = None
    force: bool = Field(default=False, strict=True)
    allowPartial: bool = Field(default=False, strict=True)
    model_config = {'extra': 'forbid'}


class DocumentQuery(BaseModel):
    """Общие координаты и ограниченные проекции, без raw SQL или callbacks."""
    snapshotId: str = Field(min_length=1, max_length=128)
    projectId: str | None = Field(default=None, max_length=128)
    documentId: str | None = Field(default=None, max_length=256)
    parentId: str | None = Field(default=None, max_length=256)
    nodeId: str | None = Field(default=None, max_length=256)
    query: str | None = Field(default=None, max_length=500)
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int | None = Field(default=None, ge=1, le=12000, strict=True)
    offset: int | None = Field(default=None, ge=0, strict=True)
    rowStart: int | None = Field(default=None, ge=0, strict=True)
    rowCount: int | None = Field(default=None, ge=1, le=1000, strict=True)
    columnStart: int | None = Field(default=None, ge=0, strict=True)
    columnCount: int | None = Field(default=None, ge=1, le=1000, strict=True)
    maxNodes: int | None = Field(default=None, ge=1, le=120, strict=True)
    model_config = {'extra': 'forbid'}


class DocumentDiscovery(BaseModel):
    """Discovery имеет отдельные recoverable страницы и фильтр по относительному пути."""
    projectId: str | None = Field(default=None, max_length=128)
    root: str | None = Field(default=None, max_length=4096)
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=100, ge=1, le=100, strict=True)
    query: str = Field(default='', max_length=500)
    model_config = {'extra': 'forbid'}


class DocumentView(BaseModel):
    """Сохранённый подграф всегда закреплён за конкретным документным snapshot."""
    snapshotId: str = Field(min_length=1, max_length=128)
    projectId: str | None = Field(default=None, max_length=128)
    documentId: str | None = Field(default=None, max_length=256)
    title: str = Field(default='Дерево документов', min_length=1, max_length=160)
    model_config = {'extra': 'forbid'}


def register_document_routes(app, projects, documents, data_maps):
    """Регистрирует интерфейс единой query service; registration не сканирует файлы."""
    @app.exception_handler(DocumentError)
    async def document_error(request, error):
        """Возвращает machine-readable причину без traceback и тела исходника."""
        return JSONResponse({'error': str(error), 'code': error.code}, status_code=error.status)

    @app.get('/api/document-capabilities')
    def capabilities():
        """Installed adapters и assets доступны без import optional parsers."""
        return {'schemaVersion': 'atlas-document-tree-v1', 'providers': available_providers(), 'ocr': False, 'buildPolicy': 'explicit-only'}

    @app.post('/api/document-files')
    def discover(body: DocumentDiscovery):
        """Перечисляет подходящие имена/stat, не открывая содержимое и не создавая jobs."""
        root = documents.root(body.projectId, body.root)
        inventory = project_file_inventory(root)
        suffixes = {suffix for _, _, formats, _ in PROVIDERS.values() for suffix in formats}
        files = []
        for filename in sorted(inventory['files']):
            if body.query.casefold() not in filename.casefold():
                continue
            path = root / filename
            if path.suffix.lower() in suffixes and not path.is_symlink() and path.is_file():
                files.append({'path': filename, 'sizeBytes': path.stat().st_size, 'format': path.suffix.lower()[1:]})
        page = catalog_page(files, 'files', cursor=body.cursor, limit=body.limit, scope=str(root) + body.query)
        return {**page, 'built': False, 'truncated': inventory['truncated']}

    @app.get('/api/document-maps')
    def catalog(projectId: str | None = None, cursor: str | None = None, limit: int = 100):
        """Каталог читается страницами без silent потери карт за первой сотней."""
        return documents.list(projectId, cursor=cursor, limit=limit)

    @app.post('/api/document-maps/ensure', status_code=202)
    def ensure(body: DocumentInput):
        """Создаёт map/job исключительно по явному POST запроса пользователя/агента."""
        return documents.ensure(files=body.files, map_id=body.mapId, project_id=body.projectId, root=body.root,
            title=body.title, strategy=body.strategy, options=body.options, context=body.context,
            force=body.force, allow_partial=body.allowPartial)

    @app.get('/api/document-maps/{map_id}')
    def overview(map_id: str, snapshotId: str | None = None, projectId: str | None = None):
        """Возвращает manifest запрошенного снимка и отдельную проверку свежести."""
        return documents.overview(map_id, snapshotId, projectId)

    @app.get('/api/document-maps/{map_id}/info')
    def map_info(map_id: str, projectId: str | None = None):
        """Показывает состояние импорта до появления первого опубликованного снимка."""
        return documents.info(map_id, projectId)

    @app.post('/api/document-maps/{map_id}/query/{operation}')
    def query(map_id: str, operation: str, body: DocumentQuery):
        """Приводит общий wire contract к внутренним snake_case именам."""
        values = body.model_dump(exclude_none=True)
        snapshot, project = values.pop('snapshotId'), values.pop('projectId', None)
        allowed = {'tree': {'documentId', 'parentId', 'cursor', 'limit'},
                   'search': {'documentId', 'query', 'cursor', 'limit'}, 'node': {'documentId', 'nodeId'},
                   'table': {'documentId', 'nodeId', 'rowStart', 'rowCount', 'columnStart', 'columnCount'},
                   'graph': {'documentId', 'maxNodes'}, 'content': {'documentId', 'nodeId', 'offset', 'limit'}}
        require(operation in allowed and set(values) <= allowed[operation], 'INVALID_QUERY', 'Параметры не соответствуют операции.')
        if operation in {'node', 'table', 'content'}:
            require(bool(values.get('nodeId')), 'INVALID_QUERY', 'Для операции нужен nodeId.')
        mapping = {'documentId': 'document_id', 'parentId': 'parent_id', 'nodeId': 'node_id',
                   'rowStart': 'row_start', 'rowCount': 'row_count', 'columnStart': 'column_start',
                   'columnCount': 'column_count', 'maxNodes': 'max_nodes'}
        return documents.query(map_id, snapshot, operation, project_id=project,
                               **{mapping.get(key, key): value for key, value in values.items()})

    @app.post('/api/document-maps/{map_id}/views')
    def create_view(map_id: str, body: DocumentView):
        """Закрепляет bounded граф вместе с исходными document/snapshot IDs."""
        return documents.create_view(map_id, body.snapshotId, body.title, body.documentId, body.projectId)

    @app.get('/api/document-maps/{map_id}/views/{view_id}')
    def saved_view(map_id: str, view_id: str, projectId: str | None = None):
        """Читает сохранённый граф только после проверки принадлежности карте."""
        return documents.saved_view(map_id, view_id, projectId)

    def map_job(job_id, project_id=None, map_id=None, cancel=False):
        """Shared gateway проверяет владение до отмены; legacy data jobs сохраняют контракт."""
        try:
            return documents.job(job_id, cancel=cancel, project_id=project_id, map_id=map_id)
        except DocumentError as error:
            if error.code != 'JOB_MISSING':
                raise
        job = data_maps.job(job_id)
        item = data_maps._map(job['mapId'])
        require(project_id is None or item.get('projectId') == project_id, 'SCOPE_MISMATCH', 'Чужое задание недоступно.', 403)
        require(map_id is None or job['mapId'] == map_id, 'SCOPE_MISMATCH', 'Задание другой карты.', 403)
        result = data_maps.job(job_id, cancel=True) if cancel else job
        return {**result, 'kind': 'data', 'projectId': item.get('projectId')}

    @app.get('/api/map-jobs/{job_id}')
    def job_status(job_id: str, projectId: str | None = None, mapId: str | None = None):
        """Читает состояние data/document задания в указанном scope."""
        return map_job(job_id, projectId, mapId)

    @app.post('/api/map-jobs/{job_id}/cancel')
    def job_cancel(job_id: str, body: dict):
        """Проверяет scope и отменяет consumer, сохраняя общую extraction других jobs."""
        require(set(body) <= {'projectId', 'mapId'}, 'INVALID_QUERY', 'Ожидается только scope задания.')
        return map_job(job_id, body.get('projectId'), body.get('mapId'), True)
