"""Bounded, read-only C# target/reference discovery; no MSBuild evaluation or package restore."""
from dataclasses import dataclass
from hashlib import sha256
from itertools import islice
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ..semantic_process import discover_executable
from ..source_viewer import SourceError, _read_regular_file


class ContextUnavailable(ValueError):
    """A local installation exists but its exact compilation context cannot be established."""


@dataclass(frozen=True)
class RoslynConfiguration:
    """Private engine paths and target settings; none are published as graph node attributes."""
    dotnet: Path
    sdk: Path
    references: tuple[Path, ...]
    context_files: tuple[Path, ...]
    framework: str
    language_version: str
    defines: tuple[str, ...]
    binding_reasons: tuple[str, ...]


def _read(root, path, limit=128 * 1024):
    """Read context only within the selected workspace through the existing no-symlink boundary."""
    relative = path.relative_to(root)
    return _read_regular_file(root, relative, max_bytes=limit)


def _version(path):
    """Sort stable local installation folders numerically without asking MSBuild to select a toolchain."""
    name = path.name
    return tuple(int(part) for part in name.split('.')) if re.fullmatch(r'\d+\.\d+\.\d+', name) else ()


def _project(root, paths):
    """Require one nearest .csproj owner for the pinned source cohort, never combine distinct projects."""
    owners = set()
    for path in paths:
        parent = (root / path).parent
        for _ in range(12):
            if not parent.is_relative_to(root):
                break
            candidates = list(islice(parent.glob('*.csproj'), 9))
            if len(candidates) > 1:
                raise ContextUnavailable('project-target-ambiguous')
            if candidates:
                owners.add(candidates[0])
                break
            if parent == root:
                break
            parent = parent.parent
    if len(owners) > 1:
        raise ContextUnavailable('multiple-project-source-cohort')
    return next(iter(owners)) if owners else None


def _project_properties(root, project):
    """Accept literal basic SDK properties; unsupported MSBuild semantics remain incompatible."""
    if project is None:
        return {}, [], False, ()
    try:
        tree = ET.fromstring(_read(root, project))
    except ET.ParseError as error:
        raise ContextUnavailable('invalid-project-xml') from error
    sdk = tree.attrib.get('Sdk', '')
    if sdk not in {'Microsoft.NET.Sdk', 'Microsoft.NET.Sdk.Web'}:
        raise ContextUnavailable('unsupported-project-sdk')
    properties, frameworks, packages, reasons = {}, [], False, []
    wanted = {'TargetFramework', 'TargetFrameworks', 'LangVersion', 'DefineConstants'}
    harmless = {'Nullable', 'ImplicitUsings'}
    for parent in tree.iter():
        if parent.get('Condition'):
            reasons.append('conditional-project-context-not-captured')
        for item in parent:
            name = item.tag.rsplit('}', 1)[-1]
            if item.get('Condition'):
                reasons.append('conditional-project-context-not-captured')
            if parent is tree and name not in {'PropertyGroup', 'ItemGroup'}:
                reasons.append('unmodeled-project-element')
            if name in {'Import', 'UsingTask', 'Target', 'Compile', 'ProjectReference'}:
                raise ContextUnavailable('project-context-requires-msbuild')
            if name in wanted:
                value = (item.text or '').strip()
                if parent.get('Condition') or item.get('Condition') or any(token in value for token in ('$(', '@(', '%(')):
                    raise ContextUnavailable('conditional-project-context')
                if name in properties and properties[name] != value:
                    raise ContextUnavailable('conflicting-project-context')
                properties[name] = value
            if name == 'ImplicitUsings' and (item.text or '').strip().lower() not in {'disable', 'false'}:
                reasons.append('implicit-usings-context-not-captured')
            if parent.tag.rsplit('}', 1)[-1] == 'PropertyGroup' and name not in wanted | harmless:
                reasons.append('unmodeled-project-property')
            if parent.tag.rsplit('}', 1)[-1] == 'ItemGroup' and name not in {'PackageReference', 'FrameworkReference'}:
                reasons.append('unmodeled-project-item')
            if name == 'PackageReference':
                packages = True
            if name == 'FrameworkReference':
                frameworks.append(item.get('Include'))
    if sdk == 'Microsoft.NET.Sdk.Web':
        frameworks.append('Microsoft.AspNetCore.App')
    if any(name != 'Microsoft.AspNetCore.App' for name in frameworks):
        raise ContextUnavailable('unsupported-framework-reference')
    return properties, frameworks, packages, tuple(dict.fromkeys(reasons))


def _closure_reasons(context, root, project):
    """Prove only a small default source closure; incomplete fragments produce candidate evidence.

    The scan is restricted to the nearest project, never the repository as a whole.
    It stops at 2048 entries, 128 directories, 16 sources or 4 MiB. A symlink,
    omitted source, stale capture or inherited MSBuild context invalidates proof.
    """
    if project is None:
        return ('explicit-fragment-without-project-closure',)
    for parent in (project.parent, *project.parent.parents):
        if any((parent / name).exists() for name in ('Directory.Build.props', 'Directory.Build.targets', 'Directory.Packages.props')):
            return ('inherited-msbuild-context-not-captured',)
    pending, directories, entries, total, found = [project.parent], 0, 0, 0, set()
    try:
        while pending:
            parent = pending.pop()
            directories += 1
            if directories > 128:
                return ('project-source-inventory-budget',)
            with os.scandir(parent) as listing:
                for entry in listing:
                    entries += 1
                    if entries > 2048:
                        return ('project-source-inventory-budget',)
                    if entry.name == '.git' or (parent == project.parent and entry.name in {'bin', 'obj'}):
                        continue
                    if entry.is_symlink():
                        return ('project-source-symlink-unproven',)
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False) and path.suffix.lower() == '.cs':
                        relative = path.relative_to(root).as_posix()
                        found.add(relative)
                        if len(found) > 16:
                            return ('project-source-inventory-budget',)
                        if relative not in context.source_hashes:
                            return ('project-source-closure-incomplete',)
                        content = _read(root, path, 1024 * 1024)
                        total += len(content)
                        if total > 4 * 1024 * 1024:
                            return ('project-source-inventory-budget',)
                        if sha256(content).hexdigest() != context.source_hashes[relative]:
                            return ('project-source-capture-stale',)
        captured = {path for path in context.source_hashes if Path(path).suffix.lower() == '.cs'}
        if found != captured:
            return ('project-source-closure-incomplete',)
    except (OSError, ValueError, SourceError):
        return ('project-source-inventory-unavailable',)
    return ()


def _reference_pack(dotnet_root, name, framework):
    """Select an existing reference pack for the exact target family; never substitute runtime assemblies."""
    major = framework.removeprefix('net').split('.', 1)[0]
    candidates = sorted(islice((dotnet_root / 'packs' / name).glob(major + '.*'), 64), key=_version)
    for package in reversed(candidates):
        directory = package / 'ref' / framework
        if _version(package) and directory.is_dir():
            references = tuple(sorted(islice(directory.glob('*.dll'), 513)))
            if references:
                return references
    raise ContextUnavailable('target-reference-pack-missing')


def _package_references(root, project, framework):
    """Read only already restored compile metadata; project-reference placeholders are not executable context."""
    assets = project.parent / 'obj' / 'project.assets.json'
    if not assets.is_file():
        raise ContextUnavailable('restored-package-context-missing')
    payload = json.loads(_read(root, assets, 4 * 1024 * 1024))
    target = payload.get('targets', {}).get(framework)
    if not isinstance(target, dict):
        raise ContextUnavailable('restored-target-context-missing')
    folders = list(payload.get('packageFolders', {}))
    if len(folders) > 8 or len(target) > 512:
        raise ContextUnavailable('restored-context-budget')
    references = []
    libraries = payload.get('libraries', {})
    for identity, entry in target.items():
        files = entry.get('compile', {})
        if not files:
            continue
        library = libraries.get(identity) or {}
        if library.get('type') != 'package':
            raise ContextUnavailable('project-reference-context-missing')
        package = library.get('path', '')
        if not package or Path(package).is_absolute() or '..' in Path(package).parts:
            raise ContextUnavailable('invalid-package-path')
        for relative in files:
            if relative.endswith('/_._'):
                continue
            item = Path(relative)
            if item.is_absolute() or '..' in item.parts or item.suffix.lower() != '.dll':
                raise ContextUnavailable('invalid-reference-path')
            found = next((Path(folder) / package / item for folder in folders
                          if Path(folder).is_absolute() and (Path(folder) / package / item).is_file()), None)
            if found is None:
                raise ContextUnavailable('restored-compile-reference-missing')
            references.append(found.resolve())
            if len(references) > 512:
                raise ContextUnavailable('reference-budget')
    return references, assets


def resolve_configuration(context, *, dotnet=None, sdk_root=None, target_framework=None,
                          reference_paths=(), defines=None, language_version=None):
    """Discover a literal local SDK target or refuse unsupported/ambiguous configuration.

    Explicit framework/reference/define options describe a host-selected source cohort.
    Otherwise one simple existing SDK project is required. No imports, project builds,
    generators, package download or arbitrary MSBuild property reconstruction occurs.
    """
    dotnet_root = Path(os.environ.get('DOTNET_ROOT', '/nonexistent'))
    executable = discover_executable('dotnet', explicit=dotnet, project_candidates=(
        dotnet_root / ('dotnet.exe' if os.name == 'nt' else 'dotnet'),
        Path('/usr/local/share/dotnet/dotnet'), Path('/usr/share/dotnet/dotnet')))
    if executable is None:
        raise FileNotFoundError('dotnet-runtime-missing')
    executable = executable.resolve()
    root = Path(context.workspace_root).resolve()
    paths = [path for path in context.source_hashes if Path(path).suffix.lower() == '.cs']
    if not paths or len(paths) > 16:
        raise ContextUnavailable('source-cohort-missing-or-over-budget')
    project = _project(root, paths)
    properties, extra_frameworks, packages, reasons = _project_properties(root, project)
    reasons += _closure_reasons(context, root, project)
    if packages:
        reasons += ('package-generated-context-not-captured',)
    if reference_paths:
        reasons += ('explicit-reference-fragment-context',)
    context_files = [project] if project else []
    framework = target_framework or properties.get('TargetFramework')
    choices = properties.get('TargetFrameworks', '').split(';')
    if framework is None or not re.fullmatch(r'net(?:[5-9]|[1-9][0-9])\.0', framework):
        raise ContextUnavailable('literal-target-framework-required')
    if properties.get('TargetFrameworks') and framework not in choices:
        raise ContextUnavailable('selected-target-not-in-project')
    if properties.get('TargetFramework') and framework != properties['TargetFramework']:
        raise ContextUnavailable('selected-target-differs-from-project')
    global_json = root / 'global.json'
    pinned_sdk = None
    if global_json.is_file():
        settings = json.loads(_read(root, global_json))
        pinned_sdk = (settings.get('sdk') or {}).get('version')
        if pinned_sdk:
            context_files.append(global_json)
    if sdk_root:
        sdk = Path(sdk_root).resolve()
        if pinned_sdk and sdk.name != pinned_sdk:
            raise ContextUnavailable('sdk-differs-from-global-json')
    elif pinned_sdk:
        if not re.fullmatch(r'\d+\.\d+\.\d+', pinned_sdk):
            raise ContextUnavailable('unsupported-sdk-selection')
        sdk = executable.parent / 'sdk' / pinned_sdk
    else:
        installed = list(islice((executable.parent / 'sdk').iterdir(), 64)) if (executable.parent / 'sdk').is_dir() else []
        candidates = sorted((path for path in installed if _version(path)), key=_version)
        if not candidates:
            raise ContextUnavailable('roslyn-sdk-missing')
        sdk = candidates[-1]
    if not all((sdk / 'Roslyn/bincore' / name).is_file() for name in ('Microsoft.CodeAnalysis.dll', 'Microsoft.CodeAnalysis.CSharp.dll')):
        raise ContextUnavailable('roslyn-assemblies-missing')
    references = list(_reference_pack(executable.parent, 'Microsoft.NETCore.App.Ref', framework))
    for name in set(extra_frameworks):
        references.extend(_reference_pack(executable.parent, name + '.Ref', framework))
    if packages:
        restored, assets = _package_references(root, project, framework)
        references.extend(restored)
        context_files.append(assets)
    for path in reference_paths:
        candidate = Path(path)
        if not candidate.is_absolute() or not candidate.is_file() or candidate.suffix.lower() != '.dll':
            raise ContextUnavailable('explicit-reference-missing')
        references.append(candidate.resolve())
    references = tuple(dict.fromkeys(references))
    if not references or len(references) > 512:
        raise ContextUnavailable('reference-budget')
    constants = defines if defines is not None else tuple(filter(None, re.split(r'[;, ]+', properties.get('DefineConstants', ''))))
    if len(constants) > 64 or any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value) for value in constants):
        raise ContextUnavailable('invalid-conditional-symbols')
    language = language_version or properties.get('LangVersion') or str(int(framework[3:].split('.')[0]) + 4) + '.0'
    return RoslynConfiguration(executable, sdk, references, tuple(context_files), framework, language, tuple(constants), tuple(dict.fromkeys(reasons)))


def configuration_fingerprint(configuration, worker):
    """Fingerprint the actual local compiler/bridge plus every metadata-reference stamp."""
    entries = []
    paths = [worker, worker.with_suffix('.runtimeconfig.json'),
             configuration.sdk / 'Roslyn/bincore/Microsoft.CodeAnalysis.dll',
             configuration.sdk / 'Roslyn/bincore/Microsoft.CodeAnalysis.CSharp.dll',
             *configuration.context_files]
    for path in paths:
        with path.open('rb') as handle:
            digest = sha256()
            while chunk := handle.read(65536):
                digest.update(chunk)
        entries.append([str(path), digest.hexdigest()])
    for path in configuration.references:
        info = path.stat()
        entries.append([str(path), info.st_size, info.st_mtime_ns, info.st_ino])
    entries.append([configuration.framework, configuration.language_version, configuration.defines])
    return sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
