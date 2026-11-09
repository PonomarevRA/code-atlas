"""Capture build markers and logical source areas once, without running project tooling."""
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath

from .source_viewer import SourceError, _read_regular_file
from .project_discovery_io import discovery_directory

DISCOVERY_VERSION = 'project-discovery-v1'
MAX_DIRECTORIES = 5000
MAX_DIRECTORY_ENTRIES = 512
MAX_MANIFESTS = 2000
MAX_MANIFEST_BYTES = 256 * 1024
MAX_TOTAL_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_SOURCE_FILES = 20000


def _source_path(node: dict) -> PurePosixPath | None:
    """Only captured relative source paths within this root participate in discovery."""
    if node.get('kind') == 'external':
        return None
    value = str(node.get('file') or node.get('source_file') or '').replace('\\', '/')
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or ':' in value or len(path.parts) > 40:
        return None
    return path if str(path) != '.' else None


def _descriptor(path: PurePosixPath, rule: dict, source_hash: str) -> dict:
    """Build projects retain the actual manifest and fingerprinted discovery evidence."""
    identifier = 'project-' + sha256(str(path).encode()).hexdigest()[:16]
    label = path.stem if path.suffix.lower() in {'.csproj', '.fsproj', '.vbproj'} else path.parent.name or 'Корневой проект'
    languages = rule.get('languages')
    language = rule['language'] if not languages or len(languages) == 1 else None
    descriptor = {'id': identifier, 'label': label, 'path': str(path.parent),
            'manifest': str(path), 'kind': 'build-project', 'language': language,
            'ruleId': rule['id'], 'modId': rule['modId'], 'modFingerprint': rule['modFingerprint'],
            'sourceKind': 'snapshot-captured-source', 'sourceHash': source_hash,
            'discoveryVersion': DISCOVERY_VERSION, 'confidence': 'build-marker'}
    if languages:
        descriptor['languages'] = sorted(languages)
    return descriptor


def _build_markers(language_rule: dict, rules: dict) -> list[dict]:
    """Combine legacy language markers with independently activated build providers.

    Build rules supply their own provenance. The language rule still owns source
    selection and logical areas; a Gradle package alone cannot infer Java/Kotlin
    source roots or activate a language provider.
    """
    providers = [language_rule]
    for rule in rules.get('buildRules', []):
        if language_rule['language'] in rule['languages']:
            providers.append({**rule, 'language': language_rule['language']})
    return providers


class _Discovery:
    """Bounded source-adjacent manifest reads shared by language rules within one build."""
    def __init__(self, root: Path):
        """Keep per-build caches only; no UI request or global source cache is introduced."""
        self.root = Path(root)
        self.directories = {}
        self.manifests = {}
        self.limited = False
        self.warnings = []
        self.bytes = 0

    def warn(self, message: str) -> None:
        """Expose failed or bounded reads without letting one project block the whole map."""
        self.limited = True
        if len(self.warnings) < 12:
            self.warnings.append(message)

    def directory_names(self, relative: PurePosixPath) -> list[str]:
        """Read at most 512 names without following symlink directories or entries."""
        key = str(relative)
        if key in self.directories:
            return self.directories[key]
        if len(self.directories) >= MAX_DIRECTORIES:
            self.warn('Достигнут бюджет каталогов поиска манифестов.')
            return []
        names = []
        try:
            with discovery_directory(self.root, relative) as directory:
                with os.scandir(directory) as entries:
                    for index, entry in enumerate(entries):
                        if index >= MAX_DIRECTORY_ENTRIES:
                            self.warn('Список файлов каталога манифестов усечён.')
                            break
                        if entry.is_file(follow_symlinks=False) or entry.is_symlink():
                            names.append(entry.name)
        except OSError:
            self.warn('Каталог манифестов недоступен или содержит symlink.')
        self.directories[key] = sorted(names)
        return self.directories[key]

    def manifest(self, relative: PurePosixPath) -> str | None:
        """Hash a regular marker file through the host nofollow reader, never a build tool."""
        key = str(relative)
        if key in self.manifests:
            return self.manifests[key]
        if len(self.manifests) >= MAX_MANIFESTS or self.bytes + MAX_MANIFEST_BYTES > MAX_TOTAL_MANIFEST_BYTES:
            self.warn('Достигнут бюджет манифестов.')
            return None
        self.manifests[key] = None
        self.bytes += MAX_MANIFEST_BYTES
        try:
            payload = _read_regular_file(self.root, Path(key), MAX_MANIFEST_BYTES)
            self.bytes -= MAX_MANIFEST_BYTES - len(payload)
            self.manifests[key] = sha256(payload).hexdigest()
        except (OSError, SourceError):
            self.warn('Манифест недоступен или превышает бюджет чтения.')
        return self.manifests[key]


def _rule_projects(discovery: _Discovery, files: list[PurePosixPath], rule: dict) -> list[dict]:
    """Probe ancestors of known source files; unindexed build manifests are still discoverable."""
    directories = set()
    for filename in files:
        directories.update(filename.parents)
    projects = {}
    names = set(rule.get('manifestNames', []))
    suffixes = set(rule.get('manifestSuffixes', []))
    for directory in sorted(directories, key=str):
        for name in discovery.directory_names(directory):
            path = directory / name
            if name not in names and path.suffix.lower() not in suffixes:
                continue
            source_hash = discovery.manifest(path)
            if source_hash:
                projects[str(path)] = _descriptor(path, rule, source_hash)
    return list(projects.values())


def _project_index(projects: list[dict], blocked_directories: set[str] | None = None) -> dict:
    """Index build markers once per language rule, avoiding a project scan for every source file."""
    by_directory = defaultdict(list)
    for project in projects:
        by_directory[project['path']].append(project)
    return {'byDirectory': by_directory, 'byManifest': {project['manifest']: project for project in projects},
            'blockedDirectories': blocked_directories or set()}


def _nearest_project(filename: PurePosixPath, index: dict) -> dict | None:
    """Nearest unambiguous build directory wins; co-located competing markers remain unknown."""
    if str(filename.parent) in index.get('blockedDirectories', set()):
        return None
    if str(filename) in index['byManifest']:
        return index['byManifest'][str(filename)]
    for parent in filename.parents:
        if str(parent) in index.get('blockedDirectories', set()):
            return None
        candidates = index['byDirectory'].get(str(parent), [])
        if candidates:
            return candidates[0] if len(candidates) == 1 else None
    return None


def _source_root(filename: PurePosixPath, markers: list[str], project: dict | None) -> PurePosixPath:
    """Match complete configured source-root segments, including nonstandard mod declarations."""
    selected = PurePosixPath(project['path']) if project else PurePosixPath('.')
    parts = filename.parts[:-1]
    selected_length = len(selected.parts)
    for marker in markers:
        expected = PurePosixPath(marker).parts
        for index in range(len(parts) - len(expected) + 1):
            end = index + len(expected)
            if parts[index:end] == expected and end >= selected_length:
                selected = PurePosixPath(*parts[:end])
                selected_length = end
    return selected


def _logical_areas(assignments: list[tuple], rule: dict) -> dict:
    """Infer shared package prefix and expose its child folders as heuristic logical areas."""
    buckets = defaultdict(list)
    for filename, project in assignments:
        root = _source_root(filename, rule.get('sourceRootMarkers', []), project)
        buckets[(project['id'] if project else '', str(root))].append((filename, project))
    result = {}
    for (_, source_root), files in buckets.items():
        parents = [filename.parent.parts for filename, _ in files]
        common = list(parents[0])
        for parent in parents[1:]:
            shared = 0
            for left, right in zip(common, parent):
                if left != right:
                    break
                shared += 1
            common = common[:shared]
        for filename, project in files:
            if len(filename.parent.parts) <= len(common):
                # A flat source tree without a build marker still needs a real
                # navigation area. A known build project retains its identity.
                if project:
                    continue
                area_path = filename.parent
            else:
                area_path = PurePosixPath(*filename.parts[:len(common) + 1])
            identity = f"{rule['language']}:{area_path}"
            descriptor = {'id': 'logical-' + sha256(identity.encode()).hexdigest()[:16],
                'label': area_path.name or 'Корень', 'path': str(area_path), 'kind': 'logical-area',
                'language': rule['language'], 'sourceRoot': source_root, 'confidence': 'folder-heuristic',
                'ruleId': rule['id'], 'modId': rule['modId'], 'modFingerprint': rule['modFingerprint'],
                'sourceKind': 'snapshot-captured-source', 'discoveryVersion': DISCOVERY_VERSION}
            if project:
                descriptor.update(buildProjectId=project['id'], buildManifest=project['manifest'], buildProject=project)
            result[str(filename)] = descriptor
    return result


def _ambiguous_area(filename: PurePosixPath, blocked_directories: set[str], rule: dict) -> dict | None:
    """Keep an unknown build boundary as a real folder, preventing later ancestor fallback."""
    for parent in filename.parents:
        if str(parent) not in blocked_directories:
            continue
        identity = f"{rule['language']}:{parent}"
        return {'id': 'logical-' + sha256(identity.encode()).hexdigest()[:16],
            'label': (parent.name or 'Корень') + ' · проект не определён',
            'path': str(parent), 'kind': 'logical-area', 'language': rule['language'],
            'sourceRoot': str(parent), 'confidence': 'folder-heuristic',
            'boundaryStatus': 'ambiguous-build-providers',
            'ruleId': rule['id'], 'modId': rule['modId'], 'modFingerprint': rule['modFingerprint'],
            'sourceKind': 'snapshot-captured-source', 'discoveryVersion': DISCOVERY_VERSION}
    return None


def capture_project_scopes(graph: dict, root: Path, rules: dict) -> dict:
    """Return copied nodes with pinned project facts; raw identities, source paths and edges survive."""
    nodes = [deepcopy(node) for node in graph.get('nodes', [])]
    by_file = defaultdict(list)
    for node in nodes:
        node.pop('codeProjectFacts', None)
        filename = _source_path(node)
        if filename is not None:
            by_file[filename].append(node)
    discovery = _Discovery(root)
    descriptors, prepared, claims = {}, [], defaultdict(set)
    for rule in rules.get('projectRules', []):
        files = sorted((path for path in by_file if path.suffix.lower() in rule['extensions']), key=str)
        if len(files) > MAX_SOURCE_FILES:
            discovery.warn('Достигнут бюджет исходных файлов для определения проектов.')
            files = files[:MAX_SOURCE_FILES]
        if not files:
            continue
        by_manifest = {}
        for provider in _build_markers(rule, rules):
            for project in _rule_projects(discovery, files, provider):
                by_manifest[project['manifest']] = project
                claims[project['manifest']].add((project['modId'], project['ruleId'], project['modFingerprint']))
        prepared.append((rule, files, list(by_manifest.values())))
    # Language-disjoint rules may coexist in the registry but still meet at one
    # physical build file. Do not let loop order choose that file's provenance.
    ambiguous = {manifest for manifest, providers in claims.items() if len(providers) > 1}
    for manifest in sorted(ambiguous):
        discovery.warn('Неоднозначные провайдеры одного манифеста: ' + manifest[:200])
    for rule, files, candidates in prepared:
        blocked_directories = {str(PurePosixPath(project['manifest']).parent)
                               for project in candidates if project['manifest'] in ambiguous}
        projects = [project for project in candidates if project['manifest'] not in ambiguous]
        project_index = _project_index(projects, blocked_directories)
        assignments = [(path, _nearest_project(path, project_index)) for path in files]
        areas = _logical_areas(assignments, rule) if rule.get('logicalFolders') else {}
        for project in projects:
            descriptors[project['id']] = project
        for filename, project in assignments:
            selected = areas.get(str(filename), project)
            if project is None:
                selected = _ambiguous_area(filename, blocked_directories, rule) or selected
            if selected:
                descriptors[selected['id']] = selected
                for node in by_file[filename]:
                    node['codeProjectFacts'] = deepcopy(selected)
    metadata = deepcopy(graph.get('metadata') or {})
    metadata['projectDiscovery'] = {'version': DISCOVERY_VERSION, 'analysisFingerprint': rules.get('fingerprint'),
        'sourceKind': 'snapshot-captured-source', 'status': 'partial' if discovery.limited else 'complete',
        'limited': discovery.limited, 'warnings': discovery.warnings, 'directories': len(discovery.directories),
        'manifests': sum(value is not None for value in discovery.manifests.values()),
        'projects': sorted(descriptors.values(), key=lambda value: value['id'])}
    return {**graph, 'nodes': nodes, 'metadata': metadata}
