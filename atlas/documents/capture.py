"""Ограниченный захват выбранных файлов внутри явно заданного корня."""

import hashlib
import os
from pathlib import Path
import stat

from .models import DocumentError, MAX_INPUT_BYTES, require


def source_path(root, filename):
    """Разрешает только относительный обычный файл; symlink на любом уровне запрещён."""
    relative = Path(filename)
    require(not relative.is_absolute() and '..' not in relative.parts and bool(relative.parts), "INVALID_PATH", "Ожидается относительный путь внутри проекта.")
    root = Path(root).resolve()
    current = root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), "INVALID_PATH", "Символические ссылки не принимаются.")
    require(current.is_file() and current.resolve().is_relative_to(root), "INVALID_PATH", "Исходный файл не найден внутри проекта.")
    return current


def _open_source(root, filename):
    """Закрепляет путь платформенным способом: openat POSIX либо Win32 handles."""
    if os.name == 'nt':
        from .capture_windows import secure_open
        return secure_open(root, filename)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    if os.open not in os.supports_dir_fd:
        require(False, 'CAPABILITY_UNAVAILABLE', 'Платформа не поддерживает безопасный захват через openat.')
    directory = os.open(Path(root).resolve(), flags | os.O_DIRECTORY)
    try:
        parts = Path(filename).parts
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], flags, dir_fd=directory)
    except OSError as error:
        raise DocumentError('INVALID_PATH', 'Исходный путь изменился или содержит ссылку.') from error
    finally:
        os.close(directory)


def capture(root, filename, destination, max_bytes=MAX_INPUT_BYTES):
    """Копирует закреплённый обычный файл и сверяет его идентичность до/после чтения."""
    source = source_path(root, filename)
    descriptor = _open_source(root, filename)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= max_bytes, "INPUT_LIMIT", "Документ превышает предел входных байтов.")
        with os.fdopen(descriptor, "rb", closefd=False) as input_file, destination.open("wb") as output:
            while chunk := input_file.read(64 * 1024):
                size += len(chunk)
                require(size <= max_bytes, "INPUT_LIMIT", "Документ превышает предел входных байтов.")
                digest.update(chunk)
                output.write(chunk)
        after = os.fstat(descriptor)
        stamp_before = (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        stamp_after = (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        current = source_path(root, filename).stat()
        require((current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'SOURCE_CHANGED', 'Путь документа заменён во время захвата.')
        require(stamp_before == stamp_after, "SOURCE_CHANGED", "Документ изменился во время захвата.")
        destination.chmod(0o444)
        return {"path": filename, "sourceHash": digest.hexdigest(), "size": size, "stamp": {"device": after.st_dev, "inode": after.st_ino, "size": after.st_size, "mtimeNs": after.st_mtime_ns, "ctimeNs": after.st_ctime_ns}}
    finally:
        os.close(descriptor)


def freshness(root, manifest):
    """Сверяет stat captured bindings; отсутствие/подмена оригинала означает stale, а не ошибку."""
    from datetime import datetime, timezone
    changed = []
    for binding in manifest['documents']:
        try:
            stat_value = source_path(root, binding['path']).stat()
            stamp = {'device': stat_value.st_dev, 'inode': stat_value.st_ino, 'size': stat_value.st_size, 'mtimeNs': stat_value.st_mtime_ns, 'ctimeNs': stat_value.st_ctime_ns}
            if stamp != binding.get('stamp'):
                changed.append(binding['path'])
        except (OSError, ValueError, DocumentError):
            changed.append(binding['path'])
    shown, shown_bytes = [], 0
    for filename in changed[:20]:
        shown_bytes += len(filename.encode('utf-8'))
        if shown_bytes > 2000:
            break
        shown.append(filename)
    status = 'stale' if changed else 'current' if manifest['documents'] else 'unavailable'
    return {'status': status, 'verification': 'size-mtime-ctime-inode', 'checkedAt': datetime.now(timezone.utc).isoformat(), 'changedFiles': shown, 'changedFileCount': len(changed), 'truncated': len(changed) > len(shown)}
