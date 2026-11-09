"""Ограниченный список файлов для декларативного выбора анализаторов, без чтения кода."""
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import subprocess
import threading

from .project_discovery_io import discovery_directory

MAX_FILES = 20000
MAX_PATH_BYTES = 2 * 1024 * 1024
SKIP_DIRECTORIES = {'.git', 'node_modules', '.venv', 'venv', 'vendor', '__pycache__', '.atlas', 'obj', 'bin'}


def _git_files(root: Path) -> tuple[bytes, bool] | None:
    """Git учитывает ignore; fsmonitor/hooks отключены, вывод и время ограничены."""
    command = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + os.devnull,
               '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', '.']
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    timer = threading.Timer(10, process.kill)
    timer.start()
    data = bytearray()
    try:
        while len(data) <= MAX_PATH_BYTES:
            block = process.stdout.read1(min(65536, MAX_PATH_BYTES + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        limited = len(data) > MAX_PATH_BYTES
        if limited:
            process.kill()
        returncode = process.wait()
        if returncode and not limited:
            return None
        return bytes(data[:MAX_PATH_BYTES]), limited
    finally:
        timer.cancel()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def _walk_files(root: Path) -> tuple[list[str], bool]:
    """Fallback для папок без Git; скрытые/служебные каталоги не обходятся."""
    pending, files, directories = [PurePosixPath('.')], [], 0
    while pending and len(files) < MAX_FILES and directories < 5000:
        relative = pending.pop()
        directories += 1
        try:
            with discovery_directory(root, relative) as directory:
                with os.scandir(directory) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 4096:
                            return sorted(files), True
                        path = relative / entry.name
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in SKIP_DIRECTORIES and not entry.name.startswith('.'):
                                pending.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(path.as_posix())
                            if len(files) >= MAX_FILES:
                                return sorted(files), True
        except OSError:
            return sorted(files), True
    return sorted(files), bool(pending)


def project_file_inventory(root: Path) -> dict:
    """Фиксирует только имена; Git ignore соблюдается в Git, fallback явно ограничен."""
    root = Path(root)
    scanned = _git_files(root)
    warnings = []
    if scanned is None:
        names, limited = _walk_files(root)
        mode = 'directory-fallback'
        warnings.append('Без Git: действуют исключения служебных каталогов; правила gitignore не применены.')
    else:
        data, limited = scanned
        parts = data.split(b'\0')
        if parts[-1]:
            parts.pop()  # Никогда не превращать обрезанный путь в другой файл.
            limited = True
        names = [os.fsdecode(value) for value in parts if value]
        mode = 'git-files'
    safe = set()
    for name in names:
        try:
            name.encode('utf-8')
        except UnicodeError:
            limited = True
            continue
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        safe.add(path.as_posix())
    files = sorted(safe)[:MAX_FILES]
    limited |= len(safe) > MAX_FILES
    fingerprint = sha256(('\0'.join(files) + f'\0{mode}:{limited}').encode('utf-8', errors='surrogateescape')).hexdigest()
    return {'version': 'project-files-v1', 'files': files, 'fingerprint': fingerprint,
            'mode': mode, 'truncated': limited, 'warnings': warnings}
