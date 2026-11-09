"""Build-time only lnav preparation; downloaded stock binaries are never auto-certified."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from uuid import uuid4

VERSION = '0.14.1'
SOURCE_SHA256 = '870c896a6973e1e973007be1d66bba3aef23b2aa68411fb5cc85bb7dd21a7fb0'
ASSETS = {
    'darwin-arm64': ('lnav-0.14.1-aarch64-macos.zip', '3b860ec5b2368e045a500c107006eee7077f9b66692dc17fcbf0b36a859f6631'),
    'linux-x86_64': ('lnav-0.14.1-linux-musl-x86_64.zip', 'ec1750f0a6962eed6bb85ad4249457894f190e961277c56d69c47eecce9e705e'),
    'linux-arm64': ('lnav-0.14.1-linux-musl-arm64.zip', 'e605ac855af193b5c7201e2fd94372febec931c5721b50fdf828b3e5af90c677'),
    'win32-x86_64': ('lnav-0.14.1-windows-x86_64.zip', '6eacbc1c540837cfef3bc555e985027d91638023462ecc96616c14ef49f521a1'),
    'win32-arm64': ('lnav-0.14.1-windows-arm64.zip', 'f11331898966ffa44e01dc2b505af518a5c09505f7124f6ae325abe69bccb60b'),
}
TOOLS = Path('/Library/Developer/CommandLineTools/usr/bin')


def sha256(path):
    """Hash complete artifact without loading native binaries into Python memory."""
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def download_verified(target, destination):
    """Explicit build command downloads one pinned official release, with a byte ceiling."""
    name, expected = ASSETS[target]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / name
    url = f'https://github.com/tstack/lnav/releases/download/v{VERSION}/{name}'
    with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as output:
        total = 0
        while chunk := response.read(64 * 1024):
            total += len(chunk)
            if total > 100 * 1024 * 1024:
                raise ValueError('lnav release exceeds download budget')
            output.write(chunk)
    if sha256(archive) != expected:
        archive.unlink()
        raise ValueError('lnav official release hash mismatch')
    return archive


def _command(*args, environment=None):
    """Run a build tool with structured arguments and bounded diagnostic output."""
    result = subprocess.run([str(argument) for argument in args], env=environment,
                            check=True, capture_output=True, text=True, timeout=60)
    return result.stdout


def _dependencies(binary, machine):
    """Read Mach-O dependencies; system libraries remain OS-provided."""
    lines = _command(TOOLS / 'otool', '-arch', machine, '-L', binary).splitlines()[1:]
    return [line.strip().split(' (', 1)[0] for line in lines]


def _resolve_dependency(name, owner):
    """Resolve only local loader paths and absolute build dependencies, never search PATH."""
    if name.startswith(('/usr/lib/', '/System/Library/')):
        return None
    if name.startswith('@loader_path/'):
        path = owner.parent / name.removeprefix('@loader_path/')
    elif name.startswith('/'):
        path = Path(name)
    else:
        raise ValueError('Unresolved non-system Mach-O dependency: ' + name)
    if not path.is_file():
        raise ValueError('Missing Mach-O dependency: ' + str(path))
    return path.resolve()


def _architecture_api():
    """Reuse runtime header validation without executing the target binary."""
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from atlas.lnav_runtime import native_architectures, normalize_architecture
    return native_architectures, normalize_architecture


def _target_architecture(binary, requested=None):
    """Select an actual Mach-O slice; certification requires a matching native Python runner."""
    inspect, normalize = _architecture_api()
    architectures = inspect(binary, system='darwin')
    current = normalize(platform.machine())
    machine = normalize(requested) if requested else current
    if machine not in {'x86_64', 'arm64'} or machine not in architectures:
        raise ValueError('Requested architecture is absent from Mach-O executable')
    if machine != current:
        raise ValueError('Certification requires a matching native architecture runner')
    return machine


def _check_macho_architecture(path, machine):
    """Each dylib must carry the selected executable slice; manifest labels are insufficient."""
    inspect, normalize = _architecture_api()
    if normalize(machine) not in inspect(path, system='darwin'):
        raise ValueError('Foreign-architecture Mach-O dependency: ' + str(path))


def _macho_build_version(path, machine):
    """Record actual LC_BUILD_VERSION rather than infer compatibility from build environment."""
    output = _command(TOOLS / 'otool', '-arch', machine, '-l', path)
    active = False
    result = {}
    for line in output.splitlines():
        fields = line.strip().split()
        if fields[:1] == ['cmd']:
            active = fields[1:] == ['LC_BUILD_VERSION']
        elif active and len(fields) == 2 and fields[0] in {'minos', 'sdk', 'platform'}:
            result[fields[0]] = fields[1]
    if 'minos' not in result:
        raise ValueError('Mach-O LC_BUILD_VERSION is missing: ' + str(path))
    return result


def _version_tuple(value):
    """Compare equivalent macOS spellings such as 13.0 and 13.0.0."""
    parts = tuple(int(part) for part in value.split('.'))
    return parts + (0,) * max(0, 3 - len(parts))


def _copy_macho_closure(binary, destination, machine):
    """Copy each non-system dylib once and rewrite all references relative to its loader."""
    _check_macho_architecture(binary, machine)
    destination.mkdir(parents=True)
    library_dir = destination / 'lib'
    library_dir.mkdir()
    executable = destination / 'lnav'
    shutil.copy2(binary, executable)
    pending = [(Path(binary).resolve(), executable)]
    copied = {}
    names = {}
    while pending:
        original, bundled = pending.pop()
        for name in _dependencies(original, machine):
            resolved = _resolve_dependency(name, original)
            if resolved is None:
                continue
            # otool lists a dylib's own install ID as its first entry.
            if resolved == original:
                continue
            _check_macho_architecture(resolved, machine)
            if resolved.name in names and names[resolved.name] != resolved:
                raise ValueError('Conflicting dylib basenames: ' + resolved.name)
            names[resolved.name] = resolved
            target = library_dir / resolved.name
            if resolved not in copied:
                shutil.copy2(resolved, target)
                copied[resolved] = target
                pending.append((resolved, target))
            relative = '@loader_path/lib/' if bundled == executable else '@loader_path/'
            _command(TOOLS / 'install_name_tool', '-change', name, relative + target.name, bundled)
        if bundled != executable:
            _command(TOOLS / 'install_name_tool', '-id', '@loader_path/' + bundled.name, bundled)
    for path in [*copied.values(), executable]:
        _command('/usr/bin/codesign', '--force', '--sign', '-', path)
    for path in [executable, *copied.values()]:
        _check_macho_architecture(path, machine)
        for name in _dependencies(path, machine):
            if not name.startswith(('/usr/lib/', '/System/Library/', '@loader_path/')):
                raise ValueError('Non-relocatable dependency survived: ' + name)
    return executable, list(copied)


def _copy_licenses(source_root, dependencies, destination):
    """Carry upstream and bundled third-party notices alongside the native closure."""
    licenses = destination / 'licenses'
    licenses.mkdir()
    for path in Path(source_root).rglob('*'):
        if path.is_file() and path.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')):
            relative = path.relative_to(source_root)
            target = licenses / 'lnav-source' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    for library in dependencies:
        # Homebrew dylibs resolve through Cellar/<formula>/<version>/lib/file.
        prefix = library.parent.parent
        candidates = []
        for directory in (prefix, prefix / '.brew', prefix / 'share'):
            if directory.is_dir():
                candidates.extend(path for path in directory.rglob('*') if path.is_file()
                                  and path.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')))
        if not candidates:
            if library.name.startswith('libsqlite3') and (prefix / 'include/sqlite3.h').is_file():
                target = licenses / library.name / 'PUBLIC-DOMAIN.txt'
                target.parent.mkdir(parents=True, exist_ok=True)
                header = (prefix / 'include/sqlite3.h').read_text()
                notice = header[:header.index('*/') + 2]
                target.write_text(notice + '\n\nUpstream dedication: https://www.sqlite.org/copyright.html\n')
                continue
            raise ValueError('Missing redistribution license for ' + str(library))
        for index, path in enumerate(sorted(set(candidates))):
            target = licenses / library.name / (str(index) + '-' + path.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    if not (licenses / 'lnav-source' / 'LICENSE').is_file():
        raise ValueError('Missing lnav BSD license')


def prepare_macos(binary, source_root, source_tarball, output_root, architecture=None, minimum_macos=None):
    """Certify patched no-curl source build, copy its closure, then prove parser execution."""
    if sys.platform != 'darwin':
        raise ValueError('Mach-O preparation must run on macOS')
    if sha256(source_tarball) != SOURCE_SHA256:
        raise ValueError('Unexpected lnav source archive')
    source_root = Path(source_root)
    converter = (source_root / 'src/file_converter_manager.cc').read_text()
    if 'external converters disabled in secure mode' not in converter or 'getenv("LNAVSECURE")' not in converter:
        raise ValueError('Required secure-mode converter patch is absent')
    if '#define HAVE_LIBCURL 1' in (source_root / 'src/config.h').read_text():
        raise ValueError('Parser-only build must not enable libcurl')
    makefile = (source_root / 'src/Makefile').read_text()
    if '\nCARGO_CMD = \n' not in makefile:
        raise ValueError('Parser-only build must disable unused Rust/web-server extensions')
    optimized = any(line.startswith(('CXXFLAGS = ', 'USER_CXXFLAGS = ')) and ('-O2' in line or '-O3' in line)
                    for line in makefile.splitlines())
    if not optimized:
        raise ValueError('Parser-only release must enable compiler optimization')
    machine = _target_architecture(binary, architecture)
    final_destination = Path(output_root) / ('darwin-' + machine)
    if final_destination.exists():
        raise ValueError('Output pack already exists; prepare in a fresh directory')
    destination = final_destination.with_name('.' + final_destination.name + '.prepare-' + uuid4().hex)
    executable, dependencies = _copy_macho_closure(Path(binary), destination, machine)
    _copy_licenses(source_root, dependencies, destination)
    build_versions = {}
    for path in [executable, *sorted((destination / 'lib').glob('*.dylib'))]:
        version_info = _macho_build_version(path, machine)
        if minimum_macos and _version_tuple(version_info['minos']) > _version_tuple(minimum_macos):
            raise ValueError('Mach-O dependency exceeds requested macOS baseline: ' + str(path))
        build_versions[str(path.relative_to(destination))] = version_info
    with tempfile.TemporaryDirectory(prefix='atlas-lnav-version-') as directory:
        version = _command(executable, '-V', environment={'HOME': directory, 'LNAVSECURE': '1', 'LANG': 'C'}).strip()
    if VERSION not in version:
        raise ValueError('Unexpected lnav binary version')
    manifest = {'schemaVersion': 1, 'platform': 'darwin-' + machine, 'version': VERSION,
                'executable': 'lnav', 'certified': True, 'secureModeConverterPatch': True,
                'withoutLibcurl': True, 'sourceSha256': SOURCE_SHA256, 'binaryVersion': version,
                'cargoExtensions': False, 'optimization': '-O2',
                'minimumMacOS': max((item['minos'] for item in build_versions.values()), key=_version_tuple),
                'machoBuildVersions': build_versions,
                'converterSourceSha256': sha256(source_root / 'src/file_converter_manager.cc')}
    manifest['files'] = {str(path.relative_to(destination)): sha256(path)
                         for path in sorted(destination.rglob('*')) if path.is_file()}
    manifest_path = destination / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    # Certification only survives after the runtime executes a real fixture.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from atlas.lnav_runtime import parse_log
        with tempfile.TemporaryDirectory(prefix='atlas-lnav-certify-') as directory:
            fixture = Path(directory) / 'fixture.log'
            fixture.write_text('2026-10-21T12:00:00.000 INFO atlas ready\n2026-10-21T12:00:01.000 ERROR atlas failed\n')
            result = parse_log(fixture, pack_root=destination)
            if len(result['rows']) != 2:
                raise ValueError('lnav parser fixture did not preserve two events')
    except BaseException:
        manifest['certified'] = False
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        raise
    destination.rename(final_destination)
    return final_destination


def main():
    """Explicit build entrypoints; runtime never imports this download/build module."""
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='operation', required=True)
    download = commands.add_parser('download')
    download.add_argument('--platform', choices=ASSETS, required=True)
    download.add_argument('--destination', type=Path, required=True)
    prepare = commands.add_parser('prepare-macos')
    prepare.add_argument('--binary', type=Path, required=True)
    prepare.add_argument('--source-root', type=Path, required=True)
    prepare.add_argument('--source-tarball', type=Path, required=True)
    prepare.add_argument('--output-root', type=Path, default=Path('resources/lnav'))
    prepare.add_argument('--architecture', choices=['x86_64', 'amd64', 'arm64', 'aarch64'])
    prepare.add_argument('--minimum-macos', help='Reject any native dependency requiring a newer macOS baseline')
    args = parser.parse_args()
    if args.operation == 'download':
        print(download_verified(args.platform, args.destination))
    else:
        print(prepare_macos(args.binary, args.source_root, args.source_tarball, args.output_root,
                            args.architecture, args.minimum_macos))


if __name__ == '__main__':
    main()
