"""
Bounded compressed replay of Graphify's own pre-resolution cache payloads.

Only this module's versioned .gz files are eligible for eviction. Snapshots and
provider exports are never removed. Cache misses or unavailable locking affect
performance, not published graph correctness.
"""

from contextlib import contextmanager
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
import zlib


VERSION = 'atlas-provider-cache-v2'
MAX_STORED_BYTES = 256 * 1024 * 1024
MAX_ENTRY_BYTES = 16 * 1024 * 1024
MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_IDLE_SECONDS = 24 * 60 * 60


def _checked(root, *parts):
    """
    Reject redirected managed paths rather than following cache symlinks.
    """
    current = Path(root)
    if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
        raise ValueError('Provider cache root must not be redirected')
    for part in parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('Provider cache path must not be redirected')
    return current


@contextmanager
def _lock(root):
    """
    Use a process-owned OS lock; a busy cache is skipped without delaying analysis.
    """
    path = _checked(root, 'cache.lock')
    handle = path.open('a+b')
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                pass
        yield acquired
    finally:
        if acquired:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _inflate(encoded):
    """
    Validate one gzip member with a bounded expanded payload and complete EOF.
    """
    if len(encoded) > MAX_COMPRESSED_BYTES:
        raise ValueError('Compressed provider cache entry is too large')
    decoder = zlib.decompressobj(31)
    payload = decoder.decompress(encoded, MAX_ENTRY_BYTES + 1)
    if len(payload) > MAX_ENTRY_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid or oversized provider cache payload')
    value = json.loads(payload)
    if (not isinstance(value, dict) or not isinstance(value.get('nodes'), list)
            or not isinstance(value.get('edges'), list) or value.get('partial')
            or not value['nodes'] and not value.get('skipped')):
        raise ValueError('Provider cache entry lacks complete extraction facts')
    return payload


class ProviderCache:
    """
    Own compressed immutable entries scoped to one root/config/provider identity.
    """

    def __init__(self, directory, namespace, *, clock=time.time, quota_bytes=MAX_STORED_BYTES):
        """
        Open only managed cache paths; quota_bytes bounds retained compressed files.
        """
        self.root = Path(directory)
        self.namespace = sha256(namespace.encode()).hexdigest()
        self.clock, self.quota_bytes = clock, quota_bytes
        self.root = _checked(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.folder = _checked(self.root, VERSION, self.namespace)
        _checked(self.root, VERSION).mkdir(parents=True, exist_ok=True)
        self.metrics = {'version': VERSION, 'namespace': self.namespace,
                        'restoredEntries': 0, 'exportedEntries': 0, 'evictedEntries': 0,
                        'skippedEntries': 0, 'quotaBytes': quota_bytes,
                        'maxEntryBytes': MAX_ENTRY_BYTES, 'storedBytes': 0}

    def _entry(self, key):
        """
        Accept only exact SHA-256 vendor keys, never caller-supplied paths.
        """
        if len(key) != 64 or any(character not in '0123456789abcdef' for character in key):
            raise ValueError('Invalid provider cache key')
        return _checked(self.root, VERSION, self.namespace, key + '.gz')

    def restore(self, key, destination):
        """
        Restore one expected live-file entry; corrupt or missing data is a cache miss.
        """
        try:
            path = self._entry(key)
            if not path.is_file() or path.stat().st_size > MAX_COMPRESSED_BYTES:
                return False
            if self.clock() - path.stat().st_mtime > MAX_IDLE_SECONDS:
                return False
            with path.open('rb') as source:
                payload = _inflate(source.read(MAX_COMPRESSED_BYTES + 1))
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            os.utime(path, (self.clock(), self.clock()))
            self.metrics['restoredEntries'] += 1
            return True
        except (OSError, ValueError, zlib.error):
            self.metrics['skippedEntries'] += 1
            return False

    def _files(self):
        """
        Inventory only regular managed entries, excluding symlinks and foreign files.
        """
        base = _checked(self.root, VERSION)
        rows = []
        for namespace in base.iterdir():
            if (not namespace.is_dir() or namespace.is_symlink()
                    or hasattr(namespace, 'is_junction') and namespace.is_junction()
                    or len(namespace.name) != 64
                    or any(character not in '0123456789abcdef' for character in namespace.name)):
                continue
            for path in namespace.iterdir():
                if (path.is_symlink() or not path.is_file() or path.suffix != '.gz'
                        or len(path.stem) != 64
                        or any(character not in '0123456789abcdef' for character in path.stem)):
                    continue
                stat = path.stat()
                rows.append((stat.st_mtime, stat.st_size, path))
        return rows

    def _make_room(self, incoming, incoming_entries=1):
        """
        Evict least recently used owned entries to satisfy count and stored byte quotas.
        """
        rows = sorted(self._files())
        total, count, now = sum(row[1] for row in rows), len(rows), self.clock()
        for stamp, size, path in rows:
            if (now - stamp <= MAX_IDLE_SECONDS and total + incoming <= self.quota_bytes
                    and count + incoming_entries <= MAX_ENTRIES):
                break
            path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                pass
            total -= size
            count -= 1
            self.metrics['evictedEntries'] += 1
        self.metrics['storedBytes'] = total

    def export(self, key, source):
        """
        Atomically retain a verified private-cache payload after successful analysis.
        """
        try:
            path = self._entry(key)
            source = Path(source)
            if not source.is_file() or source.stat().st_size > MAX_ENTRY_BYTES:
                self.metrics['skippedEntries'] += 1
                return False
            payload = source.read_bytes()
            encoded = gzip.compress(payload, compresslevel=6, mtime=0)
            if len(encoded) > min(MAX_COMPRESSED_BYTES, self.quota_bytes):
                self.metrics['skippedEntries'] += 1
                return False
            _inflate(encoded)
            with _lock(self.root) as acquired:
                if not acquired:
                    self.metrics['skippedEntries'] += 1
                    return False
                if path.is_file() and not path.is_symlink():
                    try:
                        if path.stat().st_size <= MAX_COMPRESSED_BYTES and _inflate(path.read_bytes()) == payload:
                            os.utime(path, (self.clock(), self.clock()))
                            self._make_room(0, incoming_entries=0)
                            return True
                    except (OSError, ValueError, zlib.error):
                        pass
                    path.unlink()
                self._make_room(len(encoded))
                self.folder.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=self.folder, prefix='entry-', suffix='.tmp', delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(encoded)
                try:
                    os.replace(temporary_path, path)
                    os.utime(path, (self.clock(), self.clock()))
                finally:
                    temporary_path.unlink(missing_ok=True)
                self.metrics['storedBytes'] += len(encoded)
                self.metrics['exportedEntries'] += 1
                return True
        except (OSError, ValueError, zlib.error):
            self.metrics['skippedEntries'] += 1
            return False

    def summary(self):
        """
        Report actual retained compressed bytes, including entries reused this run.
        """
        try:
            self.metrics['storedBytes'] = sum(row[1] for row in self._files())
        except OSError:
            self.metrics['usageStatus'] = 'unavailable'
        return dict(self.metrics)
