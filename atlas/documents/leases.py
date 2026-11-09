"""Межпроцессный барьер чтения/импорта и удаления immutable document artifacts."""
from contextlib import contextmanager
import errno
from functools import wraps
import os
from pathlib import Path
from threading import local
import time

from .models import DocumentError, require

_STATE = local()


def _windows_lock(file, *, exclusive=False, unlock=False):
    """LockFileEx поддерживает shared readers и отзывчивую отмену Windows imports."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class Overlapped(ctypes.Structure):
        """Win32 OVERLAPPED использует pointer-sized поля на x86 и x64."""
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = kernel.UnlockFileEx if unlock else kernel.LockFileEx
    arguments = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    if not unlock:
        arguments.append(wintypes.DWORD)
    function.argtypes = [*arguments, ctypes.POINTER(Overlapped)]
    function.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(file.fileno())
    offset = Overlapped()
    if unlock:
        success = function(handle, 0, 1, 0, ctypes.byref(offset))
    else:
        flags = 1 | (2 if exclusive else 0)  # FAIL_IMMEDIATELY | optional EXCLUSIVE_LOCK
        success = function(handle, flags, 0, 1, 0, ctypes.byref(offset))
    if not success:
        code = ctypes.get_last_error()
        if code in (32, 33, 158):
            raise OSError(errno.EAGAIN, 'Document storage lock is held')
        raise ctypes.WinError(code)


def _try_lock(file, exclusive):
    """ОС удерживает shared/exclusive byte-range lock до release или завершения процесса."""
    if os.name == 'nt':
        _windows_lock(file, exclusive=exclusive)
    else:
        import fcntl
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(file.fileno(), mode | fcntl.LOCK_NB)


def _unlock(file):
    """Явно освобождает lock до закрытия дескриптора; после crash ОС делает это сама."""
    if os.name == 'nt':
        _windows_lock(file, unlock=True)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def storage_lease(root, *, exclusive=False, wait=True):
    """Защищает scope от GC; вложенные вызовы одного потока переиспользуют lease.

    Reader/writer ждёт максимум 10 секунд. GC не ждёт активного reader/writer.
    Lock не имеет TTL: долгий запрос остаётся защищённым, crash не оставляет pin.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    require(not root.is_symlink(), 'RETENTION_UNSAFE', 'Каталог документов не должен быть ссылкой.', 409)
    path = root / '.artifacts.lock'
    key = str(path.resolve())
    active = getattr(_STATE, 'leases', {})
    _STATE.leases = active
    if key in active:
        require(not exclusive or active[key], 'STORE_BUSY', 'Очистка недоступна во время чтения или импорта.', 409)
        yield
        return
    require(not path.is_symlink(), 'RETENTION_UNSAFE', 'Файл блокировки не должен быть ссылкой.', 409)
    with path.open('a+b') as file:
        if path.stat().st_size == 0:
            file.write(b'0')
            file.flush()
        deadline = time.monotonic() + (10 if wait else 0)
        while True:
            try:
                _try_lock(file, exclusive)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise DocumentError('LEASE_UNAVAILABLE', 'Не удалось безопасно заблокировать хранилище.', 503) from error
                if not wait or time.monotonic() >= deadline:
                    raise DocumentError('STORE_BUSY', 'Хранилище занято чтением, импортом или очисткой.', 409) from error
                time.sleep(0.02)
        active[key] = exclusive
        try:
            yield
        finally:
            del active[key]
            _unlock(file)


def leased(method):
    """Охраняет полную public операцию store; вложенные queries переиспользуют lock."""
    @wraps(method)
    def protected(self, *args, **kwargs):
        """Не даёт GC удалить immutable artifacts до возврата операции."""
        with storage_lease(self.root):
            return method(self, *args, **kwargs)
    return protected
