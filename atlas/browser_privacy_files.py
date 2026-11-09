"""Private installation files and pinned offline SDK; no agent or capture input controls paths."""
import hashlib
import types
import io
import tempfile
from threading import RLock
from zipfile import ZipFile
import json
import os
from pathlib import Path
import platform
import stat
import sys
from uuid import uuid4
from .runtime_paths import resource_root


_native_lock = RLock()
_native_directory = None
_native_libraries = {}


class PrivacyFailure(RuntimeError):
    """A fixed failure prevents accidentally publishing raw exception fragments."""


def private_read(path, maximum):
    """Reject links, special files and group/world-readable installation credentials."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum or (os.name != 'nt' and info.st_mode & 0o077):
            raise PrivacyFailure('browser-privacy-unavailable')
        return stream.read(maximum + 1)


def private_write(path, value):
    """Publish one complete private file atomically; never follow a destination symlink."""
    path = Path(path)
    if path.is_symlink():
        raise PrivacyFailure('browser-privacy-unavailable')
    temporary = path.with_name('.' + path.name + '-' + uuid4().hex)
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sdk_files():
    """Validate pinned shipped assets before importing an adapter or loading a native library."""
    root = resource_root() / 'vendor/ag-privacy'
    manifest_bytes = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != '52baf9af21c15a5cf70e465a58fec5c121339462c9616429b02470ba54e93ec8':
        raise PrivacyFailure('browser-privacy-unavailable')
    manifest = json.loads(manifest_bytes)
    verified = {}
    if manifest['sourceHead'] != 'b0405848c9a2517d16cb17c133ad002670ab6894':
        raise PrivacyFailure('browser-privacy-unavailable')
    arch = 'arm64' if platform.machine().lower() in {'arm64', 'aarch64'} else 'x64'
    rid = ('osx' if sys.platform == 'darwin' else 'win' if sys.platform == 'win32' else 'linux') + '-' + arch
    filename = 'AgPrivacy.Native.dll' if sys.platform == 'win32' else 'AgPrivacy.Native.dylib' if sys.platform == 'darwin' else 'AgPrivacy.Native.so'
    needed = ['adapter.py', 'policy.json', 'runtime-template.json', rid + '/' + filename]
    library = root / needed[-1]
    for name in needed:
        path = root / name
        if name == needed[-1] and not path.exists():
            # App bundles cannot contain unsigned .NET Mach-O as nested code. Store exact SDK bytes
            # as data, verify the selected entry, then load one private process-lifetime copy.
            archive_path = root / 'native-runtime.zip'
            if archive_path.is_symlink() or archive_path.stat().st_size > 8 * 1024 * 1024:
                raise PrivacyFailure('browser-privacy-unavailable')
            with ZipFile(io.BytesIO(archive_path.read_bytes())) as archive:
                info = archive.getinfo(name)
                if info.file_size > 4 * 1024 * 1024:
                    raise PrivacyFailure('browser-privacy-unavailable')
                with archive.open(info) as stream:
                    data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != manifest['files'].get(name):
                raise PrivacyFailure('browser-privacy-unavailable')
            library = private_native_library(data, manifest['files'][name], filename)
        else:
            data = path.read_bytes()
            if path.is_symlink() or hashlib.sha256(data).hexdigest() != manifest['files'].get(name):
                raise PrivacyFailure('browser-privacy-unavailable')
        verified[name] = data
    # Loading by filename could execute an unverified, timestamp-valid __pycache__ entry.
    # Compile only the exact bytes whose digest was checked above, without consulting a loader cache.
    module = types.ModuleType('_atlas_agprivacy_verified')
    module.__file__ = str(root / 'adapter.py')
    exec(compile(verified['adapter.py'], module.__file__, 'exec'), module.__dict__)
    return root, library, module.VerifiedAgPrivacy, verified['policy.json'], verified['runtime-template.json']


def private_native_library(data, digest, filename):
    """Reuse one verified private library per digest; no capture data or keys enter this cache."""
    global _native_directory
    with _native_lock:
        path = _native_libraries.get(digest)
        if path is None:
            if _native_directory is None:
                _native_directory = tempfile.TemporaryDirectory(prefix='atlas-privacy-runtime-')
            path = Path(_native_directory.name) / filename
            private_write(path, data)
            _native_libraries[digest] = path
        if path.is_symlink() or hashlib.sha256(private_read(path, 4 * 1024 * 1024)).hexdigest() != digest:
            raise PrivacyFailure('browser-privacy-unavailable')
        return path
