"""Обнаружение XML только выбранных локальных .NET зависимостей; restore не запускается."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET

from .reference_documents import ReferenceError

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_SOURCES = 2048
MAX_PROJECTS = 128
MAX_WALK = 40000
MAX_DEPENDENCIES = 20000
LOCALE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def cache_roots() -> tuple[list[Path], list[Path]]:
    """Доверяем локальным путям SDK/cache из окружения приложения, не путям из assets."""
    packages = [Path.home() / '.nuget/packages']
    if os.environ.get('NUGET_PACKAGES'):
        packages.append(Path(os.environ['NUGET_PACKAGES']).expanduser())
    packs = [Path(p) / 'packs' for p in ('/usr/local/share/dotnet', '/usr/share/dotnet', '/opt/homebrew/share/dotnet')]
    packs.append(Path.home() / '.dotnet/packs')
    if os.environ.get('DOTNET_ROOT'):
        packs.append(Path(os.environ['DOTNET_ROOT']) / 'packs')
    return [p.resolve() for p in packages], [p.resolve() for p in packs]


def below(root: Path, relative: str) -> Path:
    """Отсекает traversal и выход через symlink до чтения файла зависимости."""
    logical = PurePosixPath(relative.replace('\\', '/'))
    if logical.is_absolute() or '..' in logical.parts or ':' in relative:
        raise ReferenceError('Unsafe library asset path')
    result = (root / logical).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ReferenceError('Library asset leaves its source directory')
    return result


def read_bytes(path: Path) -> bytes:
    """Ограничивает память для JSON/XML; изменение размера во время чтения не обходит лимит."""
    with path.open('rb') as stream:
        result = stream.read(MAX_INPUT_BYTES + 1)
    if len(result) > MAX_INPUT_BYTES:
        raise ReferenceError('Library input exceeds 64 MiB')
    return result


def xml_root(content: bytes):
    """UTF-8/UTF-16 XML читает только текст: entities/include/inheritdoc не выполняются."""
    if content.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = content.decode('utf-16')
    else:
        text = content.decode('utf-8-sig')
    markup = re.sub(r'<!\[CDATA\[.*?\]\]>|<!--.*?-->', '', text, flags=re.S)
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', markup, re.I):
        raise ReferenceError('XML entities are not supported')
    try:
        return ET.fromstring(text)
    except ET.ParseError as error:
        raise ReferenceError('Invalid library XML') from error


def project_manifests(root: Path) -> tuple[list[str], bool]:
    """Ограниченный обход ищет .csproj и не заходит в output/cache/VCS каталоги."""
    result, visited = [], 0
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in {'.git', 'bin', 'obj', 'node_modules', '.venv', '.atlas'} and not (Path(parent) / d).is_symlink())
        visited += len(files) + len(directories)
        for name in sorted(files):
            if name.endswith('.csproj') and not (Path(parent) / name).is_symlink():
                result.append((Path(parent) / name).relative_to(root).as_posix())
                if len(result) >= MAX_PROJECTS:
                    return result, True
        if visited >= MAX_WALK:
            return result, True
    return result, False


class LibraryDiscovery:
    """Разрешает выбранные compile assets; отсутствие документации остаётся предупреждением."""

    def __init__(self, packages=None, packs=None):
        self.packages, self.packs = cache_roots() if packages is None else (packages, packs or [])
        self.packages = [Path(p).resolve() for p in self.packages]
        self.packs = [Path(p).resolve() for p in self.packs]

    def discover(self, root: Path, manifest: str = '') -> dict:
        """Возвращает bounded inventory источников, хеш входов и честную полноту разрешения."""
        root = root.resolve()
        manifests, truncated = project_manifests(root)
        if manifest:
            if manifest not in manifests:
                raise ReferenceError('Select a discovered .csproj')
            selected = [manifest]
        else:
            selected = manifests
        self.sources, self.warnings, self.inputs = {}, [], {}
        self.truncated = truncated
        self.scanned_dependencies = 0
        for filename in selected:
            project = below(root, filename)
            try:
                self._project(root, project, filename)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.warn(f'{filename}: {error}')
            if self.scanned_dependencies >= MAX_DEPENDENCIES:
                self.truncated = True
                break
        stamp = hashlib.sha256(json.dumps(self.inputs, sort_keys=True).encode()).hexdigest()
        return {'manifests': manifests, 'selectedManifest': manifest, 'sources': list(self.sources.values()),
                'warnings': self.warnings, 'truncated': self.truncated, 'inputFingerprint': stamp,
                'inputs': self.inputs, 'offline': True}

    def warn(self, text: str):
        """Список отказов ограничен независимо от числа отсутствующих XML."""
        if len(self.warnings) < 100:
            self.warnings.append(text[:1000])
        else:
            self.truncated = True

    def _input(self, path: Path, name: str) -> bytes:
        """Fingerprint фиксирует содержимое csproj/assets, а не дату просмотра."""
        content = read_bytes(path)
        self.inputs[name] = hashlib.sha256(content).hexdigest()
        return content

    def _project(self, root: Path, project: Path, filename: str):
        """Assets задаёт точные NuGet версии; HintPath читается внутри зарегистрированного корня."""
        document = xml_root(self._input(project, filename))
        for node in document.iter():
            if node.tag.rsplit('}', 1)[-1] != 'Reference':
                continue
            hint = next((child.text for child in node if child.tag.rsplit('}', 1)[-1] == 'HintPath'), None)
            if hint and '$(' not in hint:
                path = (project.parent / hint.replace('\\', '/')).resolve()
                if path.is_relative_to(root):
                    self._assembly(path.with_suffix('.xml'), root, node.get('Include', path.stem).split(',')[0],
                                   'unknown', 'local-reference', filename, 'unresolved', path.relative_to(root).as_posix())
        assets = project.parent / 'obj/project.assets.json'
        if not assets.resolve().is_relative_to(root):
            raise ReferenceError('Assets leaves the project root')
        if not assets.is_file():
            self.inputs[filename + ':assets'] = 'missing'
            self.warn(f'{filename}: project.assets.json missing; restore dependencies outside Atlas to resolve versions')
            return
        value = json.loads(self._input(assets, assets.relative_to(root).as_posix()))
        if project.stat().st_mtime_ns > assets.stat().st_mtime_ns:
            self.warn(f'{filename}: csproj is newer than restored assets; dependency resolution may be stale')
        folders = value.get('packageFolders', {})
        allowed = [p for p in self.packages if str(p) in {str(Path(f).resolve()) for f in folders}]
        for target, libraries in value.get('targets', {}).items():
            for identity, library in libraries.items():
                self.scanned_dependencies += 1
                if self.scanned_dependencies >= MAX_DEPENDENCIES:
                    self.truncated = True
                    return
                if library.get('type') != 'package' or '/' not in identity:
                    continue
                name, version = identity.rsplit('/', 1)
                package_path = value.get('libraries', {}).get(identity, {}).get('path')
                if not package_path:
                    continue
                bases = [below(p, package_path) for p in allowed]
                base = next((p for p in bases if p.is_dir()), None)
                if base is None:
                    self.warn(f'{filename}: installed package unavailable: {identity}')
                    continue
                for asset in library.get('compile', {}):
                    if not asset.lower().endswith('.dll'):
                        continue
                    self._assembly(below(base, asset).with_suffix('.xml'), base, name, version,
                                   'nuget', filename, target, asset)
        for target, info in value.get('project', {}).get('frameworks', {}).items():
            for dependency in info.get('downloadDependencies', []):
                name = dependency.get('name', '')
                if not name.endswith('.Ref'):
                    continue
                match = re.fullmatch(r'\[([^, ]+),\s*([^, ]+)\]', dependency.get('version', ''))
                if not match or match[1] != match[2]:
                    self.warn(f'{filename}: unresolved reference pack version: {name}')
                    continue
                version = match[1]
                relative = f'{name}/{version}/ref/{target}'
                base = next((below(p, relative) for p in self.packs if below(p, relative).is_dir()), None)
                if base is None:
                    self.warn(f'{filename}: exact reference pack unavailable: {name}/{version}/{target}')
                    continue
                for path in sorted(base.glob('*.xml'))[:512]:
                    self._assembly(path, base, name, version, 'reference-pack', filename, target, path.with_suffix('.dll').name)

    def _assembly(self, path, boundary, package, version, kind, project, target, asset):
        """Переводы ищутся рядом с той же сборкой; чужой TFM или новая версия не подставляются."""
        variants = [(path, 'en')]
        if path.parent.is_dir():
            for directory in sorted(path.parent.iterdir()):
                if directory.is_dir() and not directory.is_symlink() and LOCALE.fullmatch(directory.name):
                    variants.append((directory / path.name, directory.name))
        found = False
        for source, locale in variants:
            resolved = source.resolve()
            if not resolved.is_relative_to(boundary.resolve()) or not resolved.is_file():
                continue
            found = True
            identity = [package, version, kind, asset, locale, str(resolved)]
            source_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
            membership = {'manifest': project, 'target': target}
            if source_id in self.sources:
                if membership not in self.sources[source_id]['usedBy']:
                    self.sources[source_id]['usedBy'].append(membership)
                continue
            if len(self.sources) >= MAX_SOURCES:
                self.truncated = True
                continue
            stat = resolved.stat()
            if stat.st_size > MAX_INPUT_BYTES:
                self.warn(f'XML exceeds 64 MiB: {package}/{source.name}')
                continue
            self.sources[source_id] = {'id': source_id, 'package': package, 'version': version,
                'kind': kind, 'asset': asset, 'assembly': source.stem, 'locale': locale,
                'sourcePath': str(resolved), 'sourceSizeBytes': stat.st_size,
                'sourceModifiedNs': stat.st_mtime_ns, 'usedBy': [membership]}
        if not found:
            self.warn(f'{project}: XML missing: {package}/{version}/{asset}')
