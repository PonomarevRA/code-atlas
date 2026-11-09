"""Directory enumeration for captured project markers; marker content uses the common safe reader."""
from contextlib import contextmanager
import os


@contextmanager
def discovery_directory(root, relative):
    """Keep POSIX traversal on nofollow descriptors while bounding Windows path enumeration."""
    if os.name == 'nt':
        current = root
        for part in (None, *relative.parts):
            if part is not None:
                current = current / part
            junction = getattr(current, 'is_junction', None)
            if current.is_symlink() or (junction and junction()):
                raise OSError('Symlink or junction directory')
        yield current
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in relative.parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)
