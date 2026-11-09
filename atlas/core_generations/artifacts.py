"""Trusted local deployment bundles for the projection-only source worker pilot."""

from dataclasses import dataclass
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import shutil

PROTOCOL = 'atlas-module-worker/v1'
SEMANTICS = 'module-analysis-v1'
STORAGE = 'no-store/v1'
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
FILES = ('atlas/__init__.py', 'atlas/module_compute.py',
         'atlas/module_analysis/__init__.py', 'atlas/module_analysis/models.py',
         'atlas/module_analysis/metrics.py', 'atlas/module_analysis/providers.py',
         'atlas/module_analysis/projection.py', 'atlas/module_analysis/sarf.py')


class GenerationError(ValueError):
    """Expose a stable lifecycle failure without leaking local artifact paths."""

    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.status = code, status


def canonical(value):
    """Encode finite JSON deterministically for identities and bounded frames."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def runtime_profile():
    """Pin the source interpreter and compute dependency, not native bundle ABI."""
    return {'python': platform.python_version(),
            'networkx': importlib.metadata.version('networkx')}


@dataclass(frozen=True)
class BundleArtifact:
    """A trusted deployment caller supplies the expected immutable manifest hash."""
    directory: Path
    bundle_hash: str


def _ordinary_path(root, relative):
    """Reject link-based source/destination traversal before local file access."""
    path = Path(root)
    if path.is_symlink() or not path.is_dir():
        raise GenerationError('artifact_invalid', 'Bundle root must be an ordinary directory', 400)
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise GenerationError('artifact_invalid', 'Bundle links are not supported', 400)
    return path


def _read_sources(root):
    """Capture the fixed compute allowlist with a total byte budget."""
    files, total = {}, 0
    for name in FILES:
        path = _ordinary_path(root, name)
        if not path.is_file() or path.stat().st_size > MAX_BUNDLE_BYTES:
            raise GenerationError('artifact_invalid', 'Missing or excessive compute source', 400)
        data = path.read_bytes()
        total += len(data)
        if total > MAX_BUNDLE_BYTES:
            raise GenerationError('artifact_limit', 'Compute source bundle exceeds budget', 413)
        files[name] = data
    return files


def build_bundle(source_root, destination, *, generation_id):
    """Snapshot approved local source; never called from an HTTP/MCP request.

    Hashes detect changed bytes, not publisher authenticity. Destination must not
    exist; neither source code nor examples are executed during this operation.
    """
    if not isinstance(generation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', generation_id):
        raise GenerationError('generation_invalid', 'Invalid generation identifier', 400)
    files = _read_sources(Path(source_root))
    destination = Path(destination)
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise GenerationError('artifact_invalid', 'Bundle destination must be new', 400)
    manifest = {'protocol': PROTOCOL, 'semantics': SEMANTICS, 'storage': STORAGE,
                'generationId': generation_id, 'runtime': runtime_profile(),
                'files': {name: sha256(data).hexdigest() for name, data in files.items()}}
    encoded = canonical(manifest)
    destination.mkdir()
    try:
        for name, data in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (destination / 'manifest.json').write_bytes(encoded)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return BundleArtifact(destination, sha256(encoded).hexdigest())


def verify_bundle(artifact):
    """Validate exact profile and source bytes before any artifact code executes."""
    if not isinstance(artifact, BundleArtifact):
        raise GenerationError('artifact_invalid', 'Expected a trusted bundle artifact', 400)
    path = _ordinary_path(artifact.directory, 'manifest.json')
    if not path.is_file() or path.stat().st_size > 16000:
        raise GenerationError('artifact_invalid', 'Missing or excessive manifest', 400)
    data = path.read_bytes()
    if sha256(data).hexdigest() != artifact.bundle_hash:
        raise GenerationError('artifact_hash', 'Manifest identity changed', 409)
    try:
        manifest = json.loads(data)
    except (ValueError, UnicodeError):
        raise GenerationError('artifact_invalid', 'Invalid manifest', 400) from None
    if (not isinstance(manifest, dict) or manifest.get('protocol') != PROTOCOL
            or manifest.get('semantics') != SEMANTICS or manifest.get('storage') != STORAGE
            or manifest.get('runtime') != runtime_profile()):
        raise GenerationError('generation_incompatible', 'Worker profile is incompatible', 409)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(manifest.get('generationId', ''))):
        raise GenerationError('artifact_invalid', 'Invalid generation identifier', 400)
    files = _read_sources(artifact.directory)
    expected = {name: sha256(value).hexdigest() for name, value in files.items()}
    if manifest.get('files') != expected:
        raise GenerationError('artifact_hash', 'Compute source identity changed', 409)
    return manifest, files


def copy_verified(artifact, destination):
    """Copy verified captured bytes into supervisor-owned staging and recheck it."""
    manifest, files = verify_bundle(artifact)
    destination = Path(destination)
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (destination / 'manifest.json').write_bytes(canonical(manifest))
    copied = BundleArtifact(destination, artifact.bundle_hash)
    verify_bundle(copied)
    return copied, manifest
