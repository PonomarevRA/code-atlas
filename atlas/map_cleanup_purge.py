"""
Purge only inventoried quarantine leaves; newly introduced material survives as a partial operation.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from .map_storage import checked_path
from .map_cleanup_inventory import CleanupError, stamp


def _identity(value):
    """
    Match the inventory stat representation for an already-open object or descriptor-relative stat.

    Args:
        value: os.stat_result returned without following a final symlink.
    Returns:
        Device/inode/mode/size/mtime/ctime identity used by the original preview.
    """
    return [value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


@contextmanager
def _parent(root_fd, relative, entries):
    """
    Open only original directory components relative to a pinned quarantine descriptor.

    Args:
        root_fd: Already opened original quarantine target directory.
        relative: Original inventoried relative leaf name, never a fresh filesystem discovery.
        entries: Original relative-path/stat identity map.
    Yields:
        Parent directory descriptor, closed after one unlink/rmdir operation.
    Raises:
        CleanupError: A path component is unknown, redirected or has been replaced.
        OSError: A platform/permission error leaves the original object unpurged.
    """
    descriptor = os.dup(root_fd)
    parts = Path(relative).parts
    current = Path()
    try:
        for part in parts[:-1]:
            current /= part
            expected = entries.get(current.as_posix())
            if expected is None or not stat.S_ISDIR(expected[2]):
                raise CleanupError('CLEANUP_UNSAFE', 'An unrecognized quarantine parent was preserved.')
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
            if _identity(os.fstat(descriptor))[:3] != expected[:3]:
                raise CleanupError('CLEANUP_UNSAFE', 'A replaced quarantine parent was preserved.')
        yield descriptor
    finally:
        os.close(descriptor)


def _portable_purge(storage_root, staged, entries):
    """
    Preserve the leaf-only policy where descriptor-relative APIs are unavailable.

    Args:
        storage_root: Trusted application root used for checked component traversal.
        staged: Checked original target-index quarantine directory.
        entries: Original manifest paths and identities, never new directory children.
    Returns:
        None when all original leaves/directories were removed; new children prevent rmdir.
    Raises:
        OSError: Busy files or newly introduced children leave a partial operation.
        CleanupError: A replaced or redirected object must be preserved.
    """
    ordered = sorted(entries, key=lambda name: (-len(Path(name).parts), name))
    for name in ordered:
        path = staged if name == '.' else staged / name
        expected = entries[name]
        checked_path(storage_root, *path.relative_to(storage_root).parts, directory=stat.S_ISDIR(expected[2]))
        if not path.exists():
            continue
        current = stamp(path)
        directory = stat.S_ISDIR(expected[2])
        if (current[:3] if directory else current) != (expected[:3] if directory else expected):
            raise CleanupError('CLEANUP_UNSAFE', 'A replaced quarantine object was preserved.')
        if directory:
            path.rmdir()
        else:
            path.unlink()


def purge_target(storage_root, staged, target):
    """
    Remove only original inventoried names, never recursively discovered later contents.

    Args:
        storage_root: Trusted application root, used only for the portable checked-path fallback.
        staged: Fixed checked private target-index path.
        target: Original confirmed inventory including each relative path and exact stat identity.
    Returns:
        None after original root removal; unrelated added/replaced objects are never selected.
    Raises:
        OSError: New children or locked files make the operation partial and retryable.
        CleanupError: A path/object identity changed; unknown material remains untouched.
    """
    entries = {name: identity for name, identity in target['entries']}
    descriptor_supported = os.name != 'nt' and os.unlink in os.supports_dir_fd and os.rmdir in os.supports_dir_fd
    if not descriptor_supported:
        _portable_purge(storage_root, staged, entries)
        return
    root_fd = os.open(staged, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if _identity(os.fstat(root_fd))[:3] != entries['.'][:3]:
            raise CleanupError('CLEANUP_UNSAFE', 'A replaced quarantine root was preserved.')
        ordered = sorted((name for name in entries if name != '.'), key=lambda name: (-len(Path(name).parts), name))
        for name in ordered:
            expected = entries[name]
            try:
                with _parent(root_fd, name, entries) as parent:
                    leaf = Path(name).name
                    try:
                        current = _identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False))
                    except FileNotFoundError:
                        continue
                    directory = stat.S_ISDIR(expected[2])
                    if (current[:3] if directory else current) != (expected[:3] if directory else expected):
                        raise CleanupError('CLEANUP_UNSAFE', 'A replaced quarantine object was preserved.')
                    if directory:
                        os.rmdir(leaf, dir_fd=parent)
                    else:
                        os.unlink(leaf, dir_fd=parent)
            except FileNotFoundError:
                continue
        # A new child makes rmdir fail; there is deliberately no recursive fallback.
        if stamp(staged)[:3] != entries['.'][:3]:
            raise CleanupError('CLEANUP_UNSAFE', 'A replaced quarantine root was preserved.')
        staged.rmdir()
    finally:
        os.close(root_fd)
