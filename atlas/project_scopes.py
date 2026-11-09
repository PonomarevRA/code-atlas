"""Принадлежность узлов подпроектам по сохранённым путям манифестов."""

from collections import Counter, defaultdict
import hashlib
from pathlib import PurePosixPath
import re


UNASSIGNED_PROJECT = "__unassigned__"
_DOTNET_SUFFIXES = {".csproj", ".fsproj", ".vbproj"}
_PACKAGE_MANIFESTS = {"package.json", "pyproject.toml", "Cargo.toml"}


def _source_path(node: dict) -> PurePosixPath | None:
    """Нормализует разделители пути без чтения диска; пути с выходом назад исключает."""
    filename = str(node.get("file") or node.get("source_file") or "")
    if not filename or node.get("kind") == "external":
        return None
    path = PurePosixPath(filename.replace("\\", "/"))
    if ".." in path.parts or str(path) == ".":
        return None
    return path


def _is_manifest(path: PurePosixPath) -> bool:
    """Распознаёт поддержанные файлы проектов, уже представленные в графе."""
    return path.suffix.lower() in _DOTNET_SUFFIXES or path.name in _PACKAGE_MANIFESTS


def _descriptor(manifest: PurePosixPath) -> dict:
    """Создаёт стабильный идентификатор и подпись проекта по пути манифеста."""
    digest = hashlib.sha256(str(manifest).encode("utf-8")).hexdigest()[:16]
    label = manifest.stem
    if manifest.name in _PACKAGE_MANIFESTS:
        label = manifest.parent.name or "Корневой проект"
    return {
        "id": "project-" + digest,
        "label": label,
        "path": str(manifest.parent),
        "manifest": str(manifest),
        "nodeCount": 0,
    }


def _captured_project(node: dict, path: PurePosixPath | None) -> dict | None:
    """Reuse host-captured scopes without reading disk; malformed or out-of-scope data is ignored."""
    facts = node.get('codeProjectFacts')
    if path is None or not isinstance(facts, dict):
        return None
    if facts.get('discoveryVersion') != 'project-discovery-v1' or facts.get('sourceKind') != 'snapshot-captured-source':
        return None
    if facts.get('kind') not in {'build-project', 'logical-area'}:
        return None
    identifier, directory = facts.get('id'), facts.get('path')
    if not isinstance(identifier, str) or not re.fullmatch(r'(?:project|logical)-[a-f0-9]{16}', identifier):
        return None
    if not isinstance(directory, str) or len(directory) > 2000:
        return None
    scope = PurePosixPath(directory)
    if not _safe_captured_path(directory, allow_root=True) or scope not in path.parents:
        return None
    if not _valid_captured_identity(facts, scope):
        return None
    result = {key: value for key, value in facts.items() if key != 'buildProject'}
    result['nodeCount'] = 0
    return result


def _safe_captured_path(value, *, allow_root=False) -> bool:
    """Validate normalized snapshot-relative evidence without resolving or reading its path."""
    if not isinstance(value, str) or not value or len(value) > 2000 or '\\' in value or ':' in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value:
        return False
    return allow_root or value != '.'


def _valid_captured_identity(facts: dict, scope: PurePosixPath) -> bool:
    """Require fingerprints, relative evidence and IDs derived from their declared build/folder."""
    for key in ('modId', 'ruleId'):
        if not isinstance(facts.get(key), str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', facts[key]):
            return False
    if not isinstance(facts.get('modFingerprint'), str) or not re.fullmatch(r'[a-f0-9]{64}', facts['modFingerprint']):
        return False
    if not isinstance(facts.get('label'), str) or not 1 <= len(facts['label']) <= 300:
        return False
    if facts['kind'] == 'build-project':
        manifest = facts.get('manifest')
        if not _safe_captured_path(manifest) or PurePosixPath(manifest).parent != scope:
            return False
        if not isinstance(facts.get('sourceHash'), str) or not re.fullmatch(r'[a-f0-9]{64}', facts['sourceHash']):
            return False
        expected = 'project-' + hashlib.sha256(manifest.encode()).hexdigest()[:16]
        return facts['id'] == expected
    language = facts.get('language')
    if not isinstance(language, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', language):
        return False
    source_root = facts.get('sourceRoot')
    if not _safe_captured_path(source_root, allow_root=True):
        return False
    if PurePosixPath(source_root) not in (scope, *scope.parents):
        return False
    expected = 'logical-' + hashlib.sha256(f'{language}:{scope}'.encode()).hexdigest()[:16]
    if facts['id'] != expected:
        return False
    manifest = facts.get('buildManifest')
    if manifest is not None:
        if not _safe_captured_path(manifest):
            return False
        if PurePosixPath(manifest).parent not in (scope, *scope.parents):
            return False
        expected_build = 'project-' + hashlib.sha256(manifest.encode()).hexdigest()[:16]
        if facts.get('buildProjectId') != expected_build:
            return False
    elif facts.get('buildProjectId') is not None:
        return False
    return True


def _nearest_saved_project(path, by_manifest: dict, by_directory: dict) -> str:
    """Return one saved owner or unassigned; nearest conflicting markers stop the ancestor search."""
    if path is None:
        return UNASSIGNED_PROJECT
    direct = by_manifest.get(str(path))
    if direct:
        return direct
    for parent in path.parents:
        candidates = by_directory.get(str(parent), [])
        if candidates:
            return candidates[0] if len(candidates) == 1 else UNASSIGNED_PROJECT
    return UNASSIGNED_PROJECT


def assign_code_projects(nodes: list[dict]) -> list[dict]:
    """Добавляет codeProjectId к переданным копиям узлов и возвращает каталог проектов.

    Источником служат захваченные codeProjectFacts или манифесты сохранённого
    графа; файловая система не читается. Выбирается ближайший каталог
    манифеста, а не совпадение подстроки. Если в одном каталоге несколько
    манифестов, принадлежность остальных файлов неоднозначна: они остаются
    нераспределёнными. Сам манифест принадлежит соответствующему проекту.
    """
    manifests = set()
    path_cache = {}
    prepared = []
    for node in nodes:
        filename = str(node.get("file") or node.get("source_file") or "")
        key = (filename, node.get("kind") == "external")
        if key not in path_cache:
            path_cache[key] = _source_path(node)
        path = path_cache[key]
        prepared.append((node, path))
        if path is not None and _is_manifest(path):
            manifests.add(path)
    projects = [_descriptor(path) for path in sorted(manifests, key=str)]
    captured = {}
    captured_by_node = {}
    for node, path in prepared:
        project = _captured_project(node, path)
        if project is None:
            continue
        captured[project['id']] = project
        captured_by_node[node['id']] = project['id']
        build = (node.get('codeProjectFacts') or {}).get('buildProject')
        if isinstance(build, dict):
            build_node = {'codeProjectFacts': build}
            parent = _captured_project(build_node, path)
            if parent and parent['kind'] == 'build-project':
                captured[parent['id']] = parent
    existing = {project['id']: project for project in projects}
    existing.update(captured)
    projects = sorted(existing.values(), key=lambda value: value['id'])
    by_manifest = {project["manifest"]: project["id"] for project in projects if project.get('manifest')}
    by_directory = defaultdict(list)
    for project in projects:
        by_directory[project["path"]].append(project["id"])
    counts = Counter()
    owner_by_path = {}
    for node, path in prepared:
        if path not in owner_by_path:
            owner_by_path[path] = _nearest_saved_project(path, by_manifest, by_directory)
        project_id = captured_by_node.get(node.get('id'), owner_by_path[path])
        node["codeProjectId"] = project_id
        counts[project_id] += 1
    for project in projects:
        project["nodeCount"] = counts[project["id"]]
    projects.append({
        "id": UNASSIGNED_PROJECT,
        "label": "Вне подпроектов",
        "path": "",
        "nodeCount": counts[UNASSIGNED_PROJECT],
    })
    return projects
