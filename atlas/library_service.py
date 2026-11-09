"""Явный импорт локальных XML в отдельный каталог, привязанный к зависимостям проекта."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from threading import RLock
from tempfile import TemporaryDirectory
from uuid import uuid4
import zipfile
import xml.etree.ElementTree as ET

from .library_sources import LibraryDiscovery, read_bytes, xml_root
from .reference_documents import ReferenceError
from .reference_service import ReferenceService
from .reference_store import ReferenceStore, import_archive, now, validate_manifest
from .store import write_json

MAX_IMPORT_BYTES = 1024 * 1024 * 1024


class CurrentLibraryStore(ReferenceStore):
    """Грузит manifests только current пакетов: количество прошлых импортов не влияет на поиск."""

    def __init__(self, folder: Path, packs: list[dict]):
        super().__init__(folder)
        self.current = []
        for expected in packs:
            pack_id = expected['id']
            if not re.fullmatch(r'reference-[0-9a-f]{32}', pack_id):
                raise ReferenceError('Invalid library catalog ID')
            directory = self.directory / pack_id
            if directory.is_symlink() or (directory / 'manifest.json').is_symlink():
                raise ReferenceError('Invalid library catalog path')
            value = json.loads((directory / 'manifest.json').read_text())
            validate_manifest(value)
            if value['id'] != pack_id:
                raise ReferenceError('Library manifest identity mismatch')
            self.current.append({**value, '_directory': str(directory), 'bundled': False})

    def catalog(self):
        """Immutable handles запроса исключают repeated I/O при сопоставлении переводов."""
        return self.current


class LibraryService:
    """Writer обновляет current inventory атомарно; старые редакции сохраняются отдельно."""

    def __init__(self, projects, embedder, discovery=None):
        self.projects, self.embedder = projects, embedder
        self.directory = projects.data_dir / 'library-documentation'
        self.discovery = discovery or LibraryDiscovery()
        self.lock, self.jobs, self.active = RLock(), {}, None
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='library-documentation')

    def root(self, project_id: str) -> Path:
        """ID проверяет реестр проектов; URL не задаёт каталог чтения или записи."""
        with self.projects.lock:
            return Path(self.projects._project(project_id)['path'])

    def folder(self, project_id: str) -> Path:
        """Каталог создаёт только writer; GET не строит справочник."""
        self.root(project_id)
        return self.directory / project_id

    def discover(self, project_id: str, manifest: str = '') -> dict:
        """Явный запрос обнаружения не импортирует XML и не меняет карты."""
        # Discovery содержит промежуточное состояние одного вызова, поэтому не разделяем его между запросами.
        discovery = LibraryDiscovery(self.discovery.packages, self.discovery.packs)
        return {'projectId': project_id, **discovery.discover(self.root(project_id), manifest)}

    def catalog(self, project_id: str) -> dict:
        """Читает только опубликованный inventory, без обхода cache и без автоматического refresh."""
        path = self.folder(project_id) / 'catalog.json'
        result = json.loads(path.read_text()) if path.is_file() else {
            'projectId': project_id, 'packs': [], 'capturedAt': None, 'warnings': [], 'selectedManifest': ''}
        with self.lock:
            active = self.jobs.get(self.active)
            result['activeJob'] = deepcopy(active) if active and active['projectId'] == project_id else None
        result.update(storagePath=str(path.parent), offline=True)
        return result

    def submit(self, project_id: str, manifest: str = '') -> dict:
        """Импорт начинается только по POST/MCP; одна задача ограничивает очередь и память."""
        self.root(project_id)
        with self.lock:
            if self.closed:
                raise ReferenceError('Atlas is shutting down', 503)
            if self.active:
                raise ReferenceError('A library import is already running', 409)
            job_id = uuid4().hex
            job = {'id': job_id, 'projectId': project_id, 'status': 'running', 'startedAt': now(), 'progress': {}}
            self.jobs[job_id] = job
            self.active = job_id
            self.executor.submit(self._run, job_id, project_id, manifest)
            return deepcopy(job)

    def job(self, project_id: str, job_id: str) -> dict:
        """Задача также привязана к проекту, чтобы scoped MCP не видел чужой результат."""
        self.root(project_id)
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job['projectId'] != project_id:
                raise ReferenceError('Library job not found', 404)
            return deepcopy(job)

    def _capture(self, project_id: str, manifest: str, progress) -> dict:
        """Снимок копирует выбранные XML в immutable SQLite; DLL/пример кода не выполняются."""
        inventory = self.discover(project_id, manifest)
        if sum(s['sourceSizeBytes'] for s in inventory['sources']) > MAX_IMPORT_BYTES:
            raise ReferenceError('Selected XML documentation exceeds 1 GiB; choose one .csproj')
        folder = self.folder(project_id)
        packs, warnings = [], list(inventory['warnings'])
        with TemporaryDirectory(prefix='atlas-library-xml-') as temporary:
            archive = Path(temporary) / 'source.zip'
            for number, source in enumerate(inventory['sources'], 1):
                try:
                    content = read_bytes(Path(source['sourcePath']))
                    parsed = xml_root(content)
                    if parsed.tag != 'doc' or parsed.find('members') is None:
                        raise ReferenceError('Expected compiler XML doc/members')
                    locale = parsed.get('{http://www.w3.org/XML/1998/namespace}lang', source['locale'])
                    # Путь RU и EN в ZIP один: DocId совпадает у локализованных сборок.
                    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
                        info = zipfile.ZipInfo('library-xml/' + source['assembly'] + '.xml', (1980, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        output.writestr(info, ET.tostring(parsed, encoding='utf-8'))
                    edition = source['kind'] + '-' + hashlib.sha256(source['asset'].encode()).hexdigest()[:16]
                    metadata = {'language': 'dotnet', 'version': source['version'], 'edition': edition,
                        'title': source['package'] + ' · ' + source['assembly'], 'locale': locale,
                        # URL описывает формат; фактический путь и digest есть в library metadata.
                        'sourceUrl': 'https://learn.microsoft.com/dotnet/csharp/language-reference/xmldoc/',
                        'license': 'Documentation supplied with installed library; see original package license',
                        'includes': ['library-xml/*.xml']}
                    imported = import_archive(archive, metadata, folder / 'language-references')
                    packs.append({**imported, 'sourceUrl': Path(source['sourcePath']).as_uri(),
                        'formatDocumentationUrl': imported['sourceUrl'], 'library': {**source, 'locale': locale,
                        'sourceHash': hashlib.sha256(content).hexdigest()}, 'sourceKind': 'project-library-xml'})
                except (ReferenceError, OSError, UnicodeError) as error:
                    raise ReferenceError(f"{source['package']}/{source['version']}/{source['assembly']}: {error}") from error
                progress(sources=number, totalSources=len(inventory['sources']), importedPacks=len(packs))
        # Assets/csproj могут измениться в ходе импорта: прошлый current остаётся доступным.
        current = self.discover(project_id, manifest)
        if current['inputFingerprint'] != inventory['inputFingerprint']:
            raise ReferenceError('Dependencies changed during capture; retry when files are stable', 409)
        before = [(s['id'], s['sourceSizeBytes'], s['sourceModifiedNs']) for s in inventory['sources']]
        after = [(s['id'], s['sourceSizeBytes'], s['sourceModifiedNs']) for s in current['sources']]
        if before != after:
            raise ReferenceError('XML sources changed during capture; retry', 409)
        if inventory['sources'] and not packs:
            raise ReferenceError('No valid XML documents imported; previous catalog retained')
        result = {'projectId': project_id, 'packs': packs, 'capturedAt': now(),
            'selectedManifest': manifest, 'inputFingerprint': inventory['inputFingerprint'],
            'inputs': inventory['inputs'], 'warnings': warnings, 'truncated': inventory['truncated'],
            'sourceCount': len(inventory['sources']), 'sizeBytes': sum(p['sizeBytes'] for p in packs)}
        write_json(folder / 'catalog.json', result)
        return result

    def _run(self, job_id, project_id, manifest):
        """Отказ не заменяет published catalog; прогресс и история ограничены текущей сессией."""
        def progress(**values):
            with self.lock:
                self.jobs[job_id]['progress'].update(values)
        try:
            result = self._capture(project_id, manifest, progress)
            with self.lock:
                self.jobs[job_id].update(status='completed', result={k: result[k] for k in ('capturedAt', 'sizeBytes', 'sourceCount', 'warnings')},
                                        packCount=len(result['packs']))
        except Exception as error:
            with self.lock:
                self.jobs[job_id].update(status='failed', error=str(error)[:1000])
        finally:
            with self.lock:
                self.jobs[job_id]['finishedAt'] = now()
                self.active = None
                while len(self.jobs) > 32:
                    del self.jobs[next(iter(self.jobs))]

    def search(self, project_id: str, query: str, pack_id: str = '', locale: str = 'en', mode: str = 'fulltext', limit: int = 10) -> dict:
        """Только current зависимости участвуют в поиске; старые версии остаются доступными для аудита."""
        catalog = self.catalog(project_id)
        by_id = {p['id']: p for p in catalog['packs']}
        if pack_id and pack_id not in by_id:
            raise ReferenceError('Pack is not in current project library catalog', 404)
        service = ReferenceService(self.folder(project_id), self.embedder)
        try:
            service.store = CurrentLibraryStore(self.folder(project_id), catalog['packs'])
            service.search_edition_limit = 2048
            result = service.search(query, pack_id=pack_id, locale=locale, mode=mode, limit=limit)
        finally:
            service.close()
        for hit in result['results']:
            hit['library'] = by_id[hit['packId']]['library']
            hit['sourceKind'] = 'project-library-xml'
            hit['sourceUrl'] = by_id[hit['packId']]['sourceUrl']
        result['searchedPackIds'] = [p['id'] for p in result['searchedPacks']]
        result_ids = {hit['packId'] for hit in result['results']}
        result['searchedPacks'] = [{**p, 'sourceUrl': by_id[p['id']]['sourceUrl'], 'library': by_id[p['id']]['library']}
                                 for p in result['searchedPacks'] if p['id'] in result_ids]
        return {**result, 'projectId': project_id, 'capturedAt': catalog['capturedAt'], 'inputFingerprint': catalog.get('inputFingerprint')}

    def document(self, project_id: str, pack_id: str, document_id: str, offset: int, limit: int) -> dict:
        """Чтение возвращает фактический путь XML, package version и memberships; примеры остаются текстом."""
        catalog = self.catalog(project_id)
        pack = next((p for p in catalog['packs'] if p['id'] == pack_id), None)
        if pack is None:
            raise ReferenceError('Pack is not in current project library catalog', 404)
        store = CurrentLibraryStore(self.folder(project_id), [pack])
        result = store.document(pack_id, document_id, offset, limit)
        result['pack']['library'] = pack['library']
        result['pack']['sourceUrl'] = pack['sourceUrl']
        result['sourceUrl'] = pack['sourceUrl']
        return {**result, 'sourceKind': 'project-library-xml', 'library': pack['library'],
                'projectId': project_id, 'capturedAt': catalog['capturedAt']}

    def close(self):
        """Ждёт единственный writer; атомарная публикация завершается до остановки."""
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True)
