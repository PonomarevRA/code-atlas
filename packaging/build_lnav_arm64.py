"""Reproducible repo-local ARM64 lnav build; no package-manager/global Python changes."""

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = '870c896a6973e1e973007be1d66bba3aef23b2aa68411fb5cc85bb7dd21a7fb0'
PACKAGES = [
    {'name': 'libunistring-1.3', 'library': 'libunistring.5.dylib',
     'url': 'https://ftp.gnu.org/gnu/libunistring/libunistring-1.3.tar.gz',
     'mirrors': ['https://mirrors.kernel.org/gnu/libunistring/libunistring-1.3.tar.gz'],
     'sha256': '8ea8ccf86c09dd801c8cac19878e804e54f707cf69884371130d20bde68386b7',
     'checksumSource': 'https://lists.nongnu.org/archive/html/info-gnu/2024-10/msg00000.html', 'flags': []},
    {'name': 'pcre2-10.49', 'library': 'libpcre2-8.0.dylib',
     'url': 'https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.49/pcre2-10.49.tar.gz',
     'sha256': '929f0b20e62879252a15886b06c89f1edef61a363cbd5826fb041080a5e557ae',
     'checksumSource': 'https://api.github.com/repos/PCRE2Project/pcre2/releases/tags/pcre2-10.49',
     'flags': ['--enable-pcre2-8', '--disable-pcre2-16', '--disable-pcre2-32', '--enable-jit',
               '--disable-pcre2grep-libz', '--disable-pcre2grep-libbz2']},
    {'name': 'libarchive-3.8.10', 'library': 'libarchive.13.dylib',
     'url': 'https://github.com/libarchive/libarchive/releases/download/v3.8.10/libarchive-3.8.10.tar.gz',
     'sha256': 'a1bffbe5152cfe869751179e7b16648c87c461cb8c46eee75062efbe164cc57c',
     'checksumSource': 'https://api.github.com/repos/libarchive/libarchive/releases/tags/v3.8.10',
     'flags': ['--without-openssl', '--without-nettle', '--without-mbedtls', '--without-libb2',
               '--without-lz4', '--without-zstd', '--without-lzma', '--without-xml2', '--without-expat',
               '--disable-bsdtar', '--disable-bsdcat', '--disable-bsdcpio', '--disable-bsdunzip']},
]
LNAV = {'name': 'lnav-0.14.1', 'url': 'https://github.com/tstack/lnav/releases/download/v0.14.1/lnav-0.14.1.tar.gz',
        'sha256': SOURCE_SHA256, 'checksumSource': 'https://api.github.com/repos/tstack/lnav/releases/tags/v0.14.1'}


def load_pack_helper():
    """Load sibling build tooling without colliding with PyPI's packaging module."""
    spec = importlib.util.spec_from_file_location('atlas_lnav_pack_build', ROOT / 'packaging/lnav_pack.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select_sdk(explicit=None):
    """Use the selected SDK, SDKROOT, or the installed CLT default; never a user-specific path."""
    candidate = explicit or os.environ.get('SDKROOT')
    if candidate is None:
        try:
            candidate = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'],
                                                stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
        except (OSError, subprocess.SubprocessError):
            candidate = '/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk'
    sdk = Path(candidate).resolve()
    if not (sdk / 'usr/include/c++/v1/initializer_list').is_file():
        raise ValueError('Select an installed macOS SDK with libc++ headers using --sdk.')
    return sdk


def select_tools(explicit=None):
    """Use native installed CLT tools directly; no license acceptance or tool installation."""
    if explicit:
        tools = Path(explicit)
    else:
        try:
            compiler = subprocess.check_output(['xcrun', '--find', 'clang'], stderr=subprocess.DEVNULL,
                                               text=True, timeout=5).strip()
            tools = Path(compiler).parent
        except (OSError, subprocess.SubprocessError):
            tools = Path('/Library/Developer/CommandLineTools/usr/bin')
    for name in ['clang', 'clang++', 'make', 'ar', 'ranlib', 'nm', 'ld']:
        if not (tools / name).is_file():
            raise ValueError('Missing installed tool: ' + str(tools / name))
    return tools.resolve()


def source_archive(package, downloads, helper):
    """Reuse only SHA-valid source bytes, with bounded pinned-URL mirror fallback."""
    downloads.mkdir(parents=True, exist_ok=True)
    destination = downloads / (package['name'] + '.tar.gz')
    if destination.exists():
        if helper.sha256(destination) != package['sha256']:
            raise ValueError('Cached source hash mismatch: ' + str(destination))
        return destination
    last_error = None
    for url in [package['url'], *package.get('mirrors', [])]:
        temporary = destination.with_suffix('.download')
        try:
            with urllib.request.urlopen(url, timeout=20) as response, temporary.open('wb') as output:
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise ValueError('Source archive exceeds byte budget')
                    output.write(chunk)
            if helper.sha256(temporary) != package['sha256']:
                raise ValueError('Downloaded source hash mismatch: ' + url)
            temporary.rename(destination)
            return destination
        except (OSError, TimeoutError) as error:
            last_error = error
        finally:
            temporary.unlink(missing_ok=True)
    raise RuntimeError('Pinned source mirrors unavailable: ' + package['name']) from last_error


def extract_source(archive_path, parent, name):
    """Extract a verified source tree without accepting escaping links or device members."""
    target = parent / name
    if not target.exists():
        parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path) as archive:
            if sum(member.size for member in archive.getmembers()) > 512 * 1024 * 1024:
                raise ValueError('Source archive expansion exceeds budget')
            archive.extractall(parent, filter='data')
    return target


def build_environment(prefix, sdk, tools, minimum_macos):
    """Pin CPU/SDK/baseline and prevent dependency discovery through an x86 Homebrew prefix."""
    environment = dict(os.environ)
    for key in ['DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH', 'LIBRARY_PATH', 'CPATH',
                'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH']:
        environment.pop(key, None)
    environment.update({'SDKROOT': str(sdk), 'MACOSX_DEPLOYMENT_TARGET': minimum_macos,
        'PATH': str(prefix / 'bin') + ':' + str(tools) + ':/usr/local/bin:/usr/bin:/bin',
        'PKG_CONFIG_LIBDIR': str(prefix / 'lib/pkgconfig'), 'PKG_CONFIG_PATH': str(prefix / 'lib/pkgconfig'),
        'CFLAGS': '-O2 -isysroot ' + str(sdk),
        'CXXFLAGS': '-O2 -isysroot ' + str(sdk) + ' -isystem ' + str(sdk / 'usr/include/c++/v1'),
        'CPPFLAGS': '-I' + str(prefix / 'include'), 'LDFLAGS': '-L' + str(prefix / 'lib'),
        'MAKE': str(tools / 'make')})
    for key, compiler in {'CC': 'clang', 'CXX': 'clang++', 'CC_FOR_BUILD': 'clang', 'CXX_FOR_BUILD': 'clang++'}.items():
        environment[key] = str(tools / compiler) + ' -arch arm64'
    environment['CFLAGS_FOR_BUILD'] = environment['CFLAGS']
    environment['CXXFLAGS_FOR_BUILD'] = environment['CXXFLAGS']
    for key in ['AR', 'RANLIB', 'NM', 'LD']:
        environment[key] = str(tools / key.lower())
    return environment


def run(command, source, log, environment):
    """Keep readable build logs while executing structured arguments without a shell."""
    with log.open('a') as output:
        output.write('\nCOMMAND: ' + repr([str(part) for part in command]) + '\n')
        output.flush()
        subprocess.run([str(part) for part in command], cwd=source, env=environment,
                       stdout=output, stderr=subprocess.STDOUT, check=True)


def copy_notices(source, destination):
    """Preserve full upstream notices, including PCRE's British-spelled LICENCE.md."""
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and path.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')):
            shutil.copy2(path, destination / path.name)


def dependency_current(package, prefix, helper, minimum_macos):
    """Reuse a completed local library only when its source pin, CPU and actual baseline agree."""
    marker = prefix / (package['name'] + '.installed')
    library = prefix / 'lib' / package['library']
    if not marker.is_file() or marker.read_text().strip() != package['sha256'] or not library.is_file():
        return False
    helper._check_macho_architecture(library, 'arm64')
    actual = helper._macho_build_version(library, 'arm64')['minos']
    return helper._version_tuple(actual) <= helper._version_tuple(minimum_macos)


def build_dependency(package, paths, helper, environment, jobs, minimum_macos):
    """Configure/compile/install one pinned dependency only into the repo-local prefix."""
    archive = source_archive(package, paths['downloads'], helper)
    source = extract_source(archive, paths['sources'], package['name'])
    if not dependency_current(package, paths['prefix'], helper, minimum_macos):
        log = paths['build'] / ('arm64-' + package['name'] + '.log')
        make = environment['MAKE']
        if (source / 'Makefile').exists():
            run([make, 'clean'], source, log, environment)
        flags = ['./configure', '--prefix=' + str(paths['prefix']), '--build=aarch64-apple-darwin',
                 '--host=aarch64-apple-darwin', '--disable-static', '--enable-shared',
                 '--disable-dependency-tracking', *package['flags']]
        run(flags, source, log, environment)
        run([make, '-j' + str(jobs)], source, log, environment)
        run([make, 'install'], source, log, environment)
        (paths['prefix'] / (package['name'] + '.installed')).write_text(package['sha256'])
    copy_notices(source, paths['prefix'] / 'licenses' / package['name'])
    print('ARM dependency ready:', package['name'], flush=True)


def build_parser(paths, helper, environment, jobs):
    """Build a separate patched lnav tree; Rust/web and libcurl features stay disabled."""
    archive = source_archive(LNAV, paths['downloads'], helper)
    source = extract_source(archive, paths['build'] / 'lnav-source-arm64', LNAV['name'])
    log = paths['build'] / 'arm64-lnav.log'
    converter = source / 'src/file_converter_manager.cc'
    if 'external converters disabled in secure mode' not in converter.read_text():
        run(['/usr/bin/patch', '-p1', '-i', ROOT / 'resources/lnav/parser-only-0.14.1.patch'], source, log, environment)
        os.utime(source / 'configure.ac', (1, 1))
    make = environment['MAKE']
    # Reconfiguration alone does not rebuild objects when dependency tracking is disabled.
    if (source / 'Makefile').exists():
        run([make, 'clean'], source, log, environment)
    run(['./configure', '--build=aarch64-apple-darwin', '--host=aarch64-apple-darwin',
         '--without-libcurl', '--without-cargo', '--disable-static', '--disable-dependency-tracking'], source, log, environment)
    run([make, '-j' + str(jobs)], source, log, environment)
    return source, archive


def verify_prepared_pack(directory, helper, minimum_macos):
    """Verify existing published bytes/CPU and actual deployment load commands without rebuilding."""
    from atlas.lnav_runtime import _checked_pack
    executable, manifest, _ = _checked_pack(directory)
    for filename in manifest['machoBuildVersions']:
        actual = helper._macho_build_version(Path(directory) / filename, 'arm64')['minos']
        if helper._version_tuple(actual) > helper._version_tuple(minimum_macos):
            raise ValueError('Published ARM parser exceeds requested macOS baseline')
    return executable


def main(argv=None):
    """Run from native ARM Python with Atlas build dependencies already available."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--toolchain-bin', type=Path)
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--minimum-macos', default='13.0')
    parser.add_argument('--output-root', type=Path, default=ROOT / 'resources/lnav')
    parser.add_argument('--verify-only', action='store_true', help='Check an existing certified pack without compiling')
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 32:
        parser.error('--jobs must be between 1 and 32')
    sys.path.insert(0, str(ROOT))
    from atlas.lnav_runtime import platform_key
    if platform_key() != 'darwin-arm64':
        parser.error('Use a native ARM64 macOS Python interpreter; no emulated certification.')
    sdk, tools = select_sdk(args.sdk), select_tools(args.toolchain_bin)
    helper = load_pack_helper()
    helper.TOOLS = tools
    if helper._version_tuple(args.minimum_macos) < (13, 0, 0):
        parser.error('The supported ARM parser build baseline starts at macOS13.0.')
    existing = args.output_root / 'darwin-arm64'
    if args.verify_only:
        print('Verified ARM parser pack:', verify_prepared_pack(existing, helper, args.minimum_macos))
        return
    if existing.exists():
        parser.error('Output already exists; use --verify-only or select a fresh --output-root.')
    build = ROOT / 'build'
    paths = {'build': build, 'downloads': build / 'arm64-lnav-downloads',
             'sources': build / 'arm64-lnav-dependency-sources', 'prefix': build / 'arm64-lnav-deps'}
    paths['prefix'].mkdir(parents=True, exist_ok=True)
    environment = build_environment(paths['prefix'], sdk, tools, args.minimum_macos)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, args.jobs)) as executor:
        futures = [executor.submit(build_dependency, item, paths, helper, environment,
                                   max(1, args.jobs // 3), args.minimum_macos) for item in PACKAGES]
        for future in futures:
            future.result()
    provenance = {'target': 'darwin-arm64', 'compiler': 'Apple clang -arch arm64 -O2',
                  'sdk': str(sdk), 'minimumMacOS': args.minimum_macos, 'dependencies': PACKAGES}
    (paths['prefix'] / 'NOTICE-dependency-sources.json').write_text(json.dumps(provenance, indent=2) + '\n')
    source, archive = build_parser(paths, helper, environment, args.jobs)
    output = helper.prepare_macos(source / 'src/lnav', source, archive, args.output_root,
                                  architecture='arm64', minimum_macos=args.minimum_macos)
    print('Certified ARM parser pack:', output)


if __name__ == '__main__':
    main()
