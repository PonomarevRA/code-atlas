"""Ограниченный обзор сохранённой карты без чтения текущего checkout."""

from collections import defaultdict
from hashlib import sha256
from pathlib import PurePosixPath
from pathlib import Path
import json
import tomllib


def capture_overview(graph: dict, root: Path) -> dict:
    """
    Сохраняет авторское описание манифеста или короткую выдержку README при сборке.

    Args:
        graph: Строящийся снимок; добавляется только metadata.projectOverview.
        root: Корень checkout. Только фиксированные корневые файлы до 64 КиБ без ссылок.
    Returns:
        Тот же граф с захваченным текстом либо явным неизвестным назначением.
    """
    from .source_viewer import _read_regular_file, SourceError
    purpose = {'text': None, 'status': 'unknown', 'provenance': []}
    for filename in ('pyproject.toml', 'package.json'):
        try:
            content = _read_regular_file(root, Path(filename), 64 * 1024)
            manifest = tomllib.loads(content.decode('utf-8-sig')) if filename.endswith('.toml') else json.loads(content)
            description = manifest.get('project', {}).get('description') if filename.endswith('.toml') else manifest.get('description')
        except (SourceError, UnicodeError, ValueError, RecursionError, AttributeError):
            continue
        if isinstance(description, str) and description.strip():
            purpose = {'text': description.strip()[:800], 'status': 'captured',
                       'provenance': [{'kind': 'snapshot-captured-manifest-description', 'path': filename,
                                       'sourceHash': sha256(content).hexdigest()}]}
            graph.setdefault('metadata', {})['projectOverview'] = {'version': 1, 'purpose': purpose}
            return graph
    for filename in ('README.md', 'README.rst', 'README.txt', 'README'):
        try:
            content = _read_regular_file(root, Path(filename), 64 * 1024)
            text = content.decode('utf-8-sig')
        except (SourceError, UnicodeError):
            continue
        paragraphs = text.split('\n\n')
        for paragraph in paragraphs:
            cleaned = ' '.join(paragraph.split())
            if not cleaned or cleaned.startswith(('#', '```', '<', '[!', '![', '|')):
                continue
            purpose = {'text': cleaned[:800], 'status': 'captured',
                       'provenance': [{'kind': 'snapshot-captured-readme-excerpt', 'path': filename,
                                       'sourceHash': sha256(content).hexdigest()}]}
            break
        break
    graph.setdefault('metadata', {})['projectOverview'] = {'version': 1, 'purpose': purpose}
    return graph


def source_role(path: str) -> str:
    """
    Классифицирует относительный путь как подсказку навигации, а не семантический факт.

    Args:
        path: Путь из закреплённого снимка; файловая система не читается.
    Returns:
        Роль исходников, тестов, примеров, внешнего или сгенерированного материала.
    """
    parts = {part.lower() for part in PurePosixPath(path).parts}
    if parts & {'vendor', 'node_modules', 'third_party', 'third-party'}:
        return 'vendor'
    if parts & {'artifacts', 'benchmark-results', 'test-results', 'generated'} or {'docs', 'reports'} <= parts:
        return 'generated'
    if parts & {'tests', 'test', '__tests__'}:
        return 'tests'
    if parts & {'examples', 'example', 'samples', 'sample'}:
        return 'examples'
    return 'source'


def _areas(graph: dict, snapshot_id: str) -> tuple[list, bool]:
    """
    Собирает до 24 областей из путей узлов, сохраняя только существующие raw ID.

    Args:
        graph: Опубликованный граф, который функция не изменяет.
        snapshot_id: Ревизия происхождения всех возвращённых путей и ID.
    Returns:
        Области и признак сокращения списка; каждый список переходов ограничен 16 ID.
    """
    groups = defaultdict(lambda: {'files': set(), 'ids': [], 'count': 0})
    for node in graph.get('nodes', []):
        filename = node.get('file') or node.get('source_file')
        if not isinstance(filename, str) or not filename or len(filename) > 2000:
            continue
        path = PurePosixPath(filename.replace('\\', '/'))
        if path.is_absolute() or '..' in path.parts:
            continue
        role = source_role(str(path))
        directory = str(path.parent)
        parts = path.parts[:-1]
        if len(parts) > 2:
            directory = '/'.join(parts[:2])
        group = groups[(role, directory)]
        group['files'].add(str(path))
        group['count'] += 1
        identifier = node.get('id')
        if isinstance(identifier, str) and len(identifier) <= 4096 and len(group['ids']) < 16:
            group['ids'].append(identifier)
    ordered = sorted(groups, key=lambda key: (-groups[key]['count'], key))
    result = []
    for role, directory in ordered[:24]:
        group = groups[(role, directory)]
        result.append({'id': 'area-' + sha256((role + ':' + directory).encode()).hexdigest()[:16],
                       'title': directory if directory != '.' else 'Файлы в корне',
                       'path': directory, 'role': role, 'reason': 'Группа по сохранённым путям файлов',
                       'nodeCount': group['count'], 'fileCount': len(group['files']),
                       'nodeIds': group['ids'],
                       'provenance': [{'kind': 'snapshot-path-rule', 'snapshotId': snapshot_id}]})
    return result, len(ordered) > 24


def snapshot_overview(graph: dict, snapshot_id: str) -> dict:
    """
    Выбирает статическое свидетельство обзора, не удерживая полный граф.

    Args:
        graph: Прочитанный канонический снимок; производное здоровье не требуется.
        snapshot_id: Ревизия путей, raw-ID и захваченного авторского описания.
    Returns:
        Только ограниченные области, назначение, диагностика и границы их происхождения.
    """
    metadata = graph.get('metadata', {})
    diagnostics = metadata.get('analysisDiagnostics', {})
    areas, truncated = _areas(graph, snapshot_id)
    captured = metadata.get('projectOverview', {}).get('purpose')
    purpose = captured if isinstance(captured, dict) else {'text': None, 'status': 'unknown', 'provenance': []}
    purpose = {**purpose, 'provenance': [{**item, 'snapshotId': snapshot_id}
                                        for item in purpose.get('provenance', [])]}
    return {'analysisStatus': diagnostics.get('analysisStatus', 'unknown'),
            'purpose': purpose, 'areas': areas, 'suggestedStarts': [], 'diagnostics': diagnostics,
            'coverage': {'areasTruncated': truncated, 'purposeCaptured': purpose.get('status') == 'captured',
                         'worktreeFreshnessKnown': False},
            'limitations': ['Роли областей определены по сохранённым путям, а не по бизнес-смыслу.',
                            'Назначение неизвестно, если текст не был сохранён при сборке.']}


def compose_project_overview(project: dict, captured: dict | None, job: dict | None = None) -> dict:
    """
    Соединяет сохранённое свидетельство с текущим состоянием регистрации и задачи.

    Args:
        project: Свежие публичные поля регистрации закреплённой ревизии.
        captured: Статическое свидетельство той же ревизии либо None при недоступной карте.
        job: Свежая последняя задача регистрации либо None.
    Returns:
        DTO прежнего формата; вызывающий копирует вложенные свидетельства перед выдачей наружу.
    """
    snapshot_id = project.get('snapshotId')
    available = captured is not None
    running = project.get('status') in {'queued', 'running'}
    failed = project.get('status') == 'failed'
    if available:
        state = 'refreshing_with_snapshot' if running else 'build_failed_with_snapshot' if failed else 'ready'
    elif running:
        state = 'building_no_snapshot'
    elif failed:
        state = 'build_failed_no_snapshot'
    else:
        state = 'missing_snapshot' if snapshot_id else 'not_built'
    evidence = captured if available else {'analysisStatus': 'unavailable',
        'purpose': {'text': None, 'status': 'unknown', 'provenance': []}, 'areas': [],
        'suggestedStarts': [], 'diagnostics': {},
        'coverage': {'areasTruncated': False, 'purposeCaptured': False, 'worktreeFreshnessKnown': False},
        'limitations': ['Роли областей определены по сохранённым путям, а не по бизнес-смыслу.',
                        'Назначение неизвестно, если текст не был сохранён при сборке.']}
    error = None
    if failed:
        error = {'code': 'BUILD_FAILED', 'message': 'Последняя сборка завершилась ошибкой. Можно запустить её повторно.', 'retryable': True}
    return {'apiVersion': 1, 'projectId': project['id'], 'snapshotId': snapshot_id,
            'state': state,
            'project': {'id': project['id'], 'name': project.get('name', project['id'])},
            'snapshot': {'available': available, 'builtAt': project.get('updatedAt'), 'stale': available and (running or failed)},
            'build': {'status': project.get('status'), 'jobId': (job or {}).get('id'), 'stage': (job or {}).get('stage'), 'error': error},
            **evidence}


def build_project_overview(project: dict, graph: dict | None, job: dict | None = None) -> dict:
    """
    Сохраняет прежний чистый интерфейс построения обзора из уже прочитанной карты.

    Args:
        project: Публичные поля регистрации, включая snapshotId.
        graph: Доступная закреплённая карта либо None при отсутствии снимка.
        job: Последняя известная задача регистрации либо None.
    Returns:
        Прежний DTO без чтения диска и автоматического запуска сборки.
    """
    captured = snapshot_overview(graph, project.get('snapshotId')) if graph is not None else None
    return compose_project_overview(project, captured, job)
