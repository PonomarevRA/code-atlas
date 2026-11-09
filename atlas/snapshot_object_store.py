"""
Immutable, checksummed gzip objects shared by a project's snapshot manifests.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import uuid4
import zlib

MAX_OBJECT_BYTES = 16 * 1024 * 1024
MAX_COMPRESSED_BYTES = 20 * 1024 * 1024
_DIGEST = re.compile(r'[0-9a-f]{64}')


def canonical_bytes(value: object) -> bytes:
    """
    Encode deterministic UTF-8 JSON without changing list ordering or Unicode text.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def checked_object_path(root: Path, digest: str, *, create: bool = False) -> Path:
    """
    Select a digest-only object below a nonredirected store; reject symlink components.

    The caller chooses the project-owned root. No manifest path is accepted here.
    Missing parents are created only for an explicitly requested write.
    """
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise ValueError('Invalid snapshot object digest')
    root = Path(root)
    # The store is always a direct sibling of managed snapshot directories.
    for current in (root.parent, root, root / digest[:2]):
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('Snapshot object directory must not be redirected')
        if current.exists() and not current.is_dir():
            raise ValueError('Snapshot object directory conflicts with a file')
    if create:
        (root / digest[:2]).mkdir(parents=True, exist_ok=True)
    path = root / digest[:2] / (digest + '.json.gz')
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('Snapshot object is not a regular file')
    return path


def read_object(root: Path, reference: dict) -> object:
    """
    Read one bounded object and verify exact compressed stream, length and SHA-256.

    A corrupt, missing, oversized, redirected or trailing-data object raises
    OSError/ValueError; no empty graph or alternate object is substituted.
    """
    if (not isinstance(reference, dict) or set(reference) != {'hash', 'bytes'}
            or type(reference.get('bytes')) is not int
            or not 0 < reference['bytes'] <= MAX_OBJECT_BYTES):
        raise ValueError('Invalid snapshot object reference')
    path = checked_object_path(root, reference.get('hash'))
    with path.open('rb') as source:
        encoded = source.read(MAX_COMPRESSED_BYTES + 1)
    if len(encoded) > MAX_COMPRESSED_BYTES:
        raise ValueError('Snapshot object exceeds compressed budget')
    decoder = zlib.decompressobj(wbits=31)
    try:
        content = decoder.decompress(encoded, reference['bytes'] + 1)
    except zlib.error as error:
        raise ValueError('Invalid snapshot object compression') from error
    if (len(content) != reference['bytes'] or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail
            or sha256(content).hexdigest() != reference['hash']):
        raise ValueError('Snapshot object integrity check failed')
    return json.loads(content)


def write_object(root: Path, value: object) -> dict:
    """
    Publish an immutable object atomically; concurrent identical writers reuse it.

    Existing content is verified before reuse. A collision/corruption is an error,
    never an overwrite of bytes referenced by a previous snapshot.
    """
    content = canonical_bytes(value)
    if not 0 < len(content) <= MAX_OBJECT_BYTES:
        raise ValueError('Snapshot object exceeds expanded budget')
    reference = {'hash': sha256(content).hexdigest(), 'bytes': len(content)}
    path = checked_object_path(root, reference['hash'], create=True)
    if path.exists():
        if canonical_bytes(read_object(root, reference)) != content:
            raise ValueError('Snapshot object hash collision')
        return reference
    compressor = zlib.compressobj(6, wbits=31)
    encoded = compressor.compress(content) + compressor.flush()
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        try:
            # Hard-link publication is no-clobber, unlike replace: an existing
            # immutable object can never be replaced by a competing writer.
            os.link(temporary, path)
        except FileExistsError:
            if canonical_bytes(read_object(root, reference)) != content:
                raise ValueError('Snapshot object hash collision')
    finally:
        temporary.unlink(missing_ok=True)
    return reference
