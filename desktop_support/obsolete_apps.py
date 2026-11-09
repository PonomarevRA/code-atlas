"""Guarded deletion of obsolete executable bundles; never a data-backup retention policy."""
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess

PRODUCTS = frozenset({'local.code-atlas.desktop', 'local.ag-design.desktop', 'local.ag-observer.desktop',
    'local.ag-tasks.desktop', 'com.ponomarev.agmemory', 'com.code-atlas.browser'})
PROTECTED_NAMES = frozenset({'.atlas', '.lancedb', 'agent-activity', 'data-schema.json', 'projects.json', 'appsettings.local.json'})
MAX_ENTRIES = 100000


class CleanupRefused(ValueError):
    """Keep an uncertain bundle rather than turning a cleanup failure into data loss."""


def bundle_identity(path):
    """Read bounded Info.plist and main identity without executing any candidate code."""
    path = Path(path).absolute()
    if path.suffix != '.app' or path.is_symlink() or path.resolve() != path:
        raise CleanupRefused('redirected-or-not-app')
    info = path / 'Contents/Info.plist'
    if info.resolve() != info or not 0 < info.stat().st_size <= 65536:
        raise CleanupRefused('unsafe-info')
    value = plistlib.loads(info.read_bytes())
    name = value.get('CFBundleExecutable')
    if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
        raise CleanupRefused('unsafe-executable-name')
    binary = path / 'Contents/MacOS' / name
    if binary.resolve() != binary or not stat.S_ISREG(binary.stat().st_mode):
        raise CleanupRefused('unsafe-executable')
    metadata = path.stat()
    return {'path': str(path), 'bundleId': value.get('CFBundleIdentifier'),
            'binary': str(binary.relative_to(path)), 'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'device': metadata.st_dev, 'inode': metadata.st_ino}


def inspect_payload(path):
    """Refuse embedded runtime stores/local secrets; bound the scan and never follow child links."""
    count = logical = 0
    def scan_error(error):
        """An unreadable subtree may contain durable data and cannot be treated as empty."""
        raise CleanupRefused('unreadable-payload') from error
    for base, directories, files in os.walk(path, followlinks=False, onerror=scan_error):
        directory = Path(base)
        for name in [*directories, *files]:
            count += 1
            if count > MAX_ENTRIES:
                raise CleanupRefused('payload-scan-budget')
            entry = directory / name
            if name.casefold() in PROTECTED_NAMES or entry.suffix.lower() in {'.lance', '.sqlite', '.sqlite3', '.db'}:
                raise CleanupRefused('embedded-data-or-local-configuration')
            metadata = entry.lstat()
            if stat.S_ISREG(metadata.st_mode):
                logical += metadata.st_size
        directories[:] = [name for name in directories if not (directory / name).is_symlink()]
    return {'entries': count, 'logicalBytes': logical}


def validate_identity_owner(identity, canonical, roots, owners):
    """Match exact inode/hash, product, approved roots and current owners without a payload scan."""
    path = Path(identity['path']).absolute()
    current = Path(canonical).absolute()
    if path == current or path in current.parents or current in path.parents:
        raise CleanupRefused('current-application')
    approved = [Path(root).absolute() for root in roots]
    if not any(root != path and root in path.parents for root in approved):
        raise CleanupRefused('outside-approved-roots')
    actual = bundle_identity(path)
    for key in ('path', 'bundleId', 'binary', 'binarySha256', 'device', 'inode'):
        if actual.get(key) != identity.get(key):
            raise CleanupRefused('candidate-changed')
    installed = bundle_identity(current)
    if actual['bundleId'] not in PRODUCTS or installed['bundleId'] != actual['bundleId']:
        raise CleanupRefused('product-identity')
    if owners(path):
        raise CleanupRefused('active-file-owner')


def validate_candidate(identity, canonical, roots, owners):
    """Validate the candidate and refuse embedded stores or unreadable payload subtrees."""
    validate_identity_owner(identity, canonical, roots, owners)
    return inspect_payload(identity['path'])


def verify_signature(path):
    """Qualify only the canonical current app; obsolete code is never launched as a cleanup probe."""
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(path)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def remove_obsolete(identity, canonical, roots, owners, *, qualify=verify_signature):
    """Delete one explicitly inventoried app after current qualification and final owner/identity checks."""
    payload = validate_candidate(identity, canonical, roots, owners)
    qualify(Path(canonical))
    # Recheck after the potentially slow signature verification; never use an earlier owner snapshot.
    validate_candidate(identity, canonical, roots, owners)
    # The last payload scan can itself take time; recheck owners after it as well.
    validate_identity_owner(identity, canonical, roots, owners)
    shutil.rmtree(identity['path'])  # Exact app root only; child symlinks are unlinked, not followed.
    return {'path': identity['path'], 'deleted': True, **payload, 'uniqueReclaimedBytes': None}
