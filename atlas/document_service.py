"""Каталог scoped document stores: явный импорт и общий доступ UI/MCP без Graphify."""
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .document_mods import active_document_rules, assert_document_rules_current, select_document_rule
from .document_catalog import catalog_page
from .documents.capture import source_path
from .documents.models import DocumentError, encode, identity, require, MAX_RESPONSE_BYTES
from .documents.providers import available_providers, resolve_provider
from .documents.storage import atomic_json, read_json
from .documents.store import DocumentStore


class ConfiguredProvider:
    """Picklable wrapper закрепляет настройки выбранного mod, не импортируя его код."""

    def __init__(self, provider, rule):
        """Закрепляет host provider и декларативные defaults выбранного мода."""
        self.provider, self.defaults = provider, rule.get('options', {})
        self.id, self.version, self.capabilities = provider.id, provider.version, provider.capabilities
        self.runtime_fingerprint = identity(provider.runtime_fingerprint, self.defaults, rule['modFingerprint'])

    def parse(self, path, options, context):
        """Явные параметры пользователя перекрывают декларативные defaults этого provider."""
        parse_context = {key: value for key, value in context.items() if key not in {'documentRules', 'documentInventory'}}
        return self.provider.parse(path, {**self.defaults, **options}, parse_context)

    def cache_context(self, path, options, context):
        """Host selection metadata не является содержимым документа и не попадает в parser."""
        context = {key: value for key, value in context.items() if key not in {'documentRules', 'documentInventory'}}
        hook = getattr(self.provider, 'cache_context', None)
        return hook(path, options, context) if hook else {key: value for key, value in context.items() if key not in {'sourcePath', 'logicalPath'}}


def scoped_provider(path, *, strategy=None, options=None, context=None):
    """Manual off и pinned declarations проверяются до выбора host parser."""
    context = context or {}
    rule = select_document_rule(context['documentRules'], path, strategy=strategy, context=context)
    require(rule is not None, 'CAPABILITY_UNAVAILABLE', 'Включите подходящий мод документов; ручное выключение сохраняется.', 409)
    provider = resolve_provider(path, strategy=rule['strategy'], options=options, context=context)
    return ConfiguredProvider(provider, rule)


class DocumentService:
    """Сохраняет bindings корней; открытие каталога не сканирует документы и не парсит их."""

    def __init__(self, project_store):
        """Открывает только каталог bindings; исходники и parsers здесь не читаются."""
        self.projects, self.data_dir = project_store, project_store.data_dir.resolve()
        self.catalog_path = self.data_dir / 'document-maps' / 'bindings.json'
        self.bindings = read_json(self.catalog_path) if self.catalog_path.exists() else {}
        self.stores, self.lock = {}, RLock()

    def root(self, project_id=None, root=None):
        """Фиксированный проект исключает подмену корня; standalone требует абсолютный каталог."""
        if project_id:
            with self.projects.lock:
                selected = Path(self.projects._project(project_id)['path']).resolve()
            require(root is None or Path(root).expanduser().resolve() == selected, 'SCOPE_MISMATCH', 'Корень не соответствует проекту.', 403)
        else:
            require(isinstance(root, str) and Path(root).expanduser().is_absolute(), 'INVALID_ROOT', 'Укажите абсолютный каталог или projectId.')
            selected = Path(root).expanduser()
            require(not any(part.is_symlink() for part in (selected, *selected.parents)), 'INVALID_ROOT', 'Ссылки в корне не поддерживаются.')
            selected = selected.resolve()
        require(selected.is_dir() and not selected.is_relative_to(self.data_dir), 'INVALID_ROOT', 'Нужен каталог вне хранилища Atlas.')
        return selected

    def _store(self, binding):
        """
        Reuse one document scope and attach its lazy workers to the project-store maintenance boundary.

        Args:
            binding: Existing document-map binding with registered root and optional project identity.
        Returns:
            Cached DocumentStore; registration alone does not submit parsing work.
        """
        key = identity(binding['root'], binding.get('projectId', ''))
        with self.lock:
            if key not in self.stores:
                def validate(configuration):
                    """Не публикует новый снимок после смены pinned document mods."""
                    context = configuration['context']
                    assert_document_rules_current(self.projects.mods, context['documentRules'], inventory=context['documentInventory'])
                self.stores[key] = DocumentStore(self.data_dir, Path(binding['root']), binding.get('projectId', ''),
                                                  provider_resolver=scoped_provider, prepublish=validate, auto_retention=True)
                from .map_maintenance import guard_executor
                maintenance = self.projects.maintenance
                for name in ('executor', 'parsers'):
                    setattr(self.stores[key], name, guard_executor(getattr(self.stores[key], name), maintenance))
            return self.stores[key]

    def binding(self, map_id, project_id=None):
        """Scope проверяется до чтения документа или отмены его задания."""
        with self.lock:
            require(map_id in self.bindings, 'MAP_MISSING', 'Карта документов не найдена.', 404)
            value = deepcopy(self.bindings[map_id])
        require(project_id is None or value.get('projectId') == project_id, 'SCOPE_MISMATCH', 'Карта принадлежит другому проекту.', 403)
        return value

    def list(self, project_id=None, *, cursor=None, limit=100):
        """Читает только служебные каталоги; original files не открываются."""
        maps = []
        with self.lock:
            bindings = deepcopy(self.bindings)
        inventories = {}
        for map_id, binding in sorted(bindings.items()):
            if project_id and binding.get('projectId') != project_id:
                continue
            store = self._store(binding)
            if store not in inventories:
                inventories[store] = {item['id']: item for item in store.maps()['maps']}
            item = inventories[store].get(map_id, {})
            maps.append({**binding, **{key: item.get(key) for key in ('status', 'snapshotId', 'builtAt', 'error')}})
        return catalog_page(maps, 'maps', cursor=cursor, limit=limit, scope=project_id or '')

    def ensure(self, *, files=None, map_id=None, project_id=None, root=None, title='Документы', strategy=None,
               options=None, context=None, force=False, allow_partial=False):
        """Запускает только явно запрошенный набор файлов и фиксирует mod selection."""
        if map_id:
            binding = self.binding(map_id, project_id)
            require(root is None or Path(root).expanduser().resolve() == Path(binding['root']), 'SCOPE_MISMATCH', 'Нельзя менять корень карты.', 403)
            store = self._store(binding)
            saved = store.catalog[map_id].get('configuration', {})
            files = files if files is not None else saved.get('sources', [])
            options = options if options is not None else saved.get('options', {})
            strategy = strategy if strategy is not None else saved.get('strategy')
            context = context if context is not None else saved.get('context', {})
        else:
            selected = self.root(project_id, root)
            map_id = uuid4().hex
            binding = {'id': map_id, 'title': title, 'root': str(selected), 'projectId': project_id or ''}
            store = self._store(binding)
        require(isinstance(files, list) and 1 <= len(files) <= 100, 'INVALID_FILES', 'Выберите 1..100 файлов.')
        for name in files:
            require(isinstance(name, str), 'INVALID_FILES', 'Имена файлов должны быть строками.')
            source_path(Path(binding['root']), name)
        inventory = {'version': 'document-selection-v1', 'files': sorted(files), 'mode': 'explicit-selection', 'truncated': False, 'warnings': []}
        rules = active_document_rules(self.projects.mods, inventory=inventory)
        context = {**(context or {}), 'documentRules': rules, 'documentInventory': inventory}
        job = store.ensure(map_id, files, options=options, strategy=strategy, context=context, force=force, allow_partial=allow_partial)
        with self.lock:
            self.bindings[map_id] = binding
            atomic_json(self.catalog_path, self.bindings)
        return {'map': binding, 'job': {**job, 'kind': 'documents', 'projectId': binding['projectId']}}

    def overview(self, map_id, snapshot_id=None, project_id=None):
        """Выдаёт выбранный immutable manifest, не подменяя исторические bindings."""
        binding = self.binding(map_id, project_id)
        store = self._store(binding)
        with store.snapshot_lease(map_id, snapshot_id) as manifest:
            return {**manifest, 'title': binding['title'], 'freshness': store.freshness(map_id, manifest['snapshotId']),
                    'url': '/documents.html?map=' + map_id + '&snapshot=' + manifest['snapshotId']}

    def info(self, map_id, project_id=None):
        """Scope доступен и до первого успешного build, чтобы failed import можно повторить."""
        binding = self.binding(map_id, project_id)
        store = self._store(binding)
        with store.lock:
            item = deepcopy(store.catalog.get(map_id, {}))
        return {**binding, **{key: item.get(key) for key in ('status', 'snapshotId', 'builtAt', 'error', 'jobId')}}

    def query(self, map_id, snapshot_id, operation, project_id=None, **query):
        """Единая служба читает индекс; неизвестные операции и параметры отклоняются."""
        binding = self.binding(map_id, project_id)
        store = self._store(binding)
        operations = {'tree': store.tree, 'search': store.search, 'node': store.node, 'table': store.table, 'graph': store.graph, 'content': store.read_content}
        require(operation in operations, 'INVALID_QUERY', 'Неизвестная операция дерева.')
        with store.snapshot_lease(map_id, snapshot_id) as manifest:
            result = operations[operation](map_id, snapshot_id=manifest['snapshotId'], **query)
            response = {**result, 'mapId': map_id, 'snapshotId': manifest['snapshotId'], 'builtAt': manifest['createdAt'],
                        'coverage': manifest['coverage'], 'freshness': store.freshness(map_id, manifest['snapshotId'])}
            require(len(encode(response).encode()) <= MAX_RESPONSE_BYTES, 'RESPONSE_LIMIT', 'Уменьшите страницу, окно или лимит текста.')
            return response

    def create_view(self, map_id, snapshot_id, title, document_id=None, project_id=None):
        """Сохраняет bounded immutable graph и pin под тем же lease, что защищает чтение."""
        binding = self.binding(map_id, project_id)
        with self._store(binding).snapshot_lease(map_id, snapshot_id) as manifest:
            graph = self.query(map_id, manifest['snapshotId'], 'graph', project_id, document_id=document_id)
            view_id = uuid4().hex
            view = {**graph, 'id': view_id, 'title': title, 'url': '/documents.html?map=' + map_id + '&view=' + view_id}
            atomic_json(self.data_dir / 'document-maps' / 'views' / (view_id + '.json'), view)
            return {key: view[key] for key in ('id', 'title', 'url', 'mapId', 'snapshotId')}

    def saved_view(self, map_id, view_id, project_id=None):
        """Читает только закреплённый вид выбранной карты, не произвольный путь."""
        self.binding(map_id, project_id)
        require(isinstance(view_id, str) and len(view_id) == 32 and all(char in '0123456789abcdef' for char in view_id), 'INVALID_VIEW', 'Неверный ID вида.')
        path = self.data_dir / 'document-maps' / 'views' / (view_id + '.json')
        require(path.is_file(), 'VIEW_MISSING', 'Вид не найден.', 404)
        view = read_json(path)
        require(view['mapId'] == map_id, 'SCOPE_MISMATCH', 'Вид другой карты.', 403)
        return view

    def job(self, job_id, *, cancel=False, project_id=None, map_id=None):
        """Находит document job в зарегистрированном scope до изменения состояния."""
        seen = set()
        with self.lock:
            bindings = deepcopy(list(self.bindings.values()))
        for binding in bindings:
            key = identity(binding['root'], binding.get('projectId', ''))
            if key in seen:
                continue
            seen.add(key)
            store = self._store(binding)
            if job_id not in store.jobs:
                continue
            job = store.job(job_id)
            owned = self.binding(job['mapId'], project_id)
            require(map_id is None or map_id == job['mapId'], 'SCOPE_MISMATCH', 'Задание принадлежит другой карте.', 403)
            result = store.cancel(job_id) if cancel else job
            return {**result, 'kind': 'documents', 'projectId': owned['projectId']}
        raise DocumentError('JOB_MISSING', 'Задание не найдено.', 404)

    def retention(self, map_id, *, project_id=None, dry_run=True):
        """Очищает общий artifact scope выбранной карты, сохраняя pins всех его карт."""
        binding = self.binding(map_id, project_id)
        report = self._store(binding).retention(dry_run=dry_run)
        return {**report, 'mapId': map_id, 'projectId': binding['projectId']}

    def close(self):
        """Завершает только созданные службы и их незавершённые process workers."""
        for store in self.stores.values():
            store.close()
