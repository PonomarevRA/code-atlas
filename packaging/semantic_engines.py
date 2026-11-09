"""Package small Atlas bridges only; installed compilers/runtimes never enter the bundle."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil

ROSLYN_FILES = ('Atlas.Semantic.Roslyn.dll', 'Atlas.Semantic.Roslyn.runtimeconfig.json')
HOST_FILES = (
    'atlas/semantic_lsp.py', 'atlas/semantic_lsp_providers.py',
    'atlas/semantic_providers/lsp_base.py', 'atlas/semantic_providers/jdt.py',
    'atlas/semantic_providers/gopls.py', 'atlas/semantic_providers/rust_analyzer.py',
    'atlas/semantic_providers/sourcekit.py', 'atlas/semantic_providers/php.py',
)


def _worker_files(directory):
    """Accept exactly the managed bridge/configuration, without walking its SDK dependencies."""
    files = [Path(directory) / name for name in ROSLYN_FILES]
    if any(not path.is_file() or not 0 < path.stat().st_size <= 256 * 1024 for path in files):
        raise ValueError('Expected a small Roslyn bridge DLL and runtimeconfig; no compiler/runtime bundle')
    options = json.loads(files[1].read_text())['runtimeOptions']
    if options.get('tfm') != 'net8.0' or options.get('framework', {}).get('name') != 'Microsoft.NETCore.App':
        raise ValueError('Expected the framework-dependent net8.0 Atlas bridge')
    if 'includedFrameworks' in options:
        raise ValueError('Self-contained runtime bundles are not supported')
    return files


def prepare_roslyn_worker(root, *, prebuilt=None):
    """At release-build time, compile Atlas's bridge offline using an already installed SDK8.

    Without a build SDK/prebuilt bridge, keep C# as a conditional profile with an
    explicit worker path. Map use never calls this function or invokes a compiler.
    """
    root = Path(root)
    if prebuilt is not None:
        files = _worker_files(prebuilt)
        return {'directory': str(files[0].parent.resolve()), 'source': 'explicit-prebuilt'}
    dotnet = shutil.which('dotnet')
    if not dotnet:
        for candidate in ('/usr/local/share/dotnet/dotnet', '/usr/share/dotnet/dotnet'):
            if Path(candidate).is_file() and os.access(candidate, os.X_OK):
                dotnet = candidate
                break
    if dotnet:
        installation = Path(dotnet).resolve().parent
        sdk_directory = installation / 'sdk'
        versions = []
        if sdk_directory.is_dir():
            for path in sdk_directory.iterdir():
                if re.fullmatch(r'8\.\d+\.\d+', path.name):
                    versions.append(path)
        sdk = max(versions, key=lambda path: tuple(map(int, path.name.split('.')))) if versions else None
        if sdk and (installation / 'packs/Microsoft.NETCore.App.Ref').is_dir():
            source = root / 'resources/semantic-csharp/build_worker.py'
            spec = importlib.util.spec_from_file_location('atlas_release_roslyn_bridge', source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output = root / 'build/semantic-csharp'
            module.build_worker(Path(dotnet), sdk, output)
            _worker_files(output)
            return {'directory': str(output.resolve()), 'source': 'existing-sdk8-offline'}
    return {'directory': None, 'source': 'unavailable-no-existing-build-sdk8'}


def semantic_worker_data(root, roslyn_directory=None):
    """Ship subprocess files and exact host identities that probes stat outside PYZ."""
    root = Path(root)
    selected = []
    for name in ('typescript_worker.cjs', 'python_worker.py'):
        path = root / 'atlas/semantic_providers' / name
        if not path.is_file():
            raise ValueError('Missing Atlas semantic worker: ' + name)
        selected.append((str(path), 'atlas/semantic_providers'))
    for relative in HOST_FILES:
        path = root / relative
        if not path.is_file():
            raise ValueError('Missing Atlas semantic host identity: ' + relative)
        selected.append((str(path), path.relative_to(root).parent.as_posix()))
    if roslyn_directory is not None:
        for path in _worker_files(roslyn_directory):
            selected.append((str(path), 'resources/semantic-csharp'))
    return selected


def semantic_worker_manifest(data):
    """Record exact shipped bytes while making absence of engine distributions explicit."""
    files = []
    for filename, destination in data:
        path = Path(filename)
        content = path.read_bytes()
        relative = destination + '/' + path.name
        files.append({'file': relative, 'kind': 'host-identity' if relative in HOST_FILES else 'worker',
                      'bytes': len(content),
                      'sha256': sha256(content).hexdigest()})
    return {'version': 1, 'engineDistributionsBundled': False, 'runtimeDownloadsRequired': False,
            'files': files, 'totalBytes': sum(item['bytes'] for item in files),
            'workerPayloadBytes': sum(item['bytes'] for item in files if item['kind'] == 'worker'),
            'hostIdentityBytes': sum(item['bytes'] for item in files if item['kind'] == 'host-identity')}
