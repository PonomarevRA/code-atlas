"""Захват Windows-файла через закреплённые Win32 handles, без обхода reparse points."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path, PureWindowsPath

from .models import DocumentError, require

REPARSE_POINT = 0x400
DIRECTORY = 0x10


class _FileInformation(ctypes.Structure):
    """Структура Win32 BY_HANDLE_FILE_INFORMATION с фиксированными DWORD полями."""

    _fields_ = [
        ('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
        ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
        ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
        ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
        ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD),
    ]


class _WinApi:
    """Минимальные Win32 операции; handles закрываются владельцем secure_open."""

    def __init__(self):
        """Загружает kernel32 только на Windows и задаёт ABI для безопасной работы с handles."""
        require(os.name == 'nt', 'CAPABILITY_UNAVAILABLE', 'Win32 capture доступен только на Windows.')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
        self.kernel.GetFileInformationByHandle.restype = wintypes.BOOL
        self.kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        self.kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL

    def open(self, path, directory):
        """Не разрешает удаление/переименование; конечный файл также запрещает запись."""
        access = 0x80 if directory else 0x80000000  # FILE_READ_ATTRIBUTES / GENERIC_READ
        share = 0x3 if directory else 0x1  # READ|WRITE для каталогов; READ для файла
        flags = 0x00200000 | 0x02000000  # OPEN_REPARSE_POINT | BACKUP_SEMANTICS
        handle = self.kernel.CreateFileW(str(path), access, share, None, 3, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), 'CreateFileW failed')
        return handle

    def attributes(self, handle):
        """Читает атрибуты самого объекта, а не цели junction/symlink."""
        information = _FileInformation()
        if not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise OSError(ctypes.get_last_error(), 'GetFileInformationByHandle failed')
        return information.attributes

    def final_path(self, handle):
        """Получает канонический путь открытого объекта с обработкой длинных путей."""
        size = self.kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not size:
            raise OSError(ctypes.get_last_error(), 'GetFinalPathNameByHandleW failed')
        buffer = ctypes.create_unicode_buffer(size + 1)
        written = self.kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not written or written >= len(buffer):
            raise OSError(ctypes.get_last_error(), 'Final path changed')
        return buffer.value

    def close(self, handle):
        """Освобождает ещё не переданный CRT файловый handle."""
        self.kernel.CloseHandle(handle)


def _canonical(path):
    """Нормализует Win32 extended path для сравнения границ каталогов без prefix-ошибок."""
    value = str(path)
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return PureWindowsPath(value)


def _descriptor(handle):
    """Передаёт владение Win32 handle CRT; os.close затем закрывает оба представления."""
    import msvcrt
    return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)


def secure_open(root, filename, *, api=None, to_descriptor=None):
    """Открывает относительный файл, удерживая каждый каталог против подмены до открытия.

    Junction, symlink, ADS и drive-relative пути запрещены. Последний handle удерживает
    файл против записи/удаления на всём протяжении capture; общий код сверяет fstat.
    """
    relative = PureWindowsPath(filename)
    require(not relative.drive and not relative.root and bool(relative.parts)
            and all(part not in {'.', '..'} and ':' not in part for part in relative.parts),
            'INVALID_PATH', 'Ожидается относительный файл без ссылок и потоков ADS.')
    api = api or _WinApi()
    transfer = to_descriptor or _descriptor
    handles = []
    try:
        current = Path(root).resolve()
        root_handle = api.open(current, True)
        handles.append(root_handle)
        require(not api.attributes(root_handle) & REPARSE_POINT, 'INVALID_PATH', 'Reparse point в корне запрещён.')
        root_path = _canonical(api.final_path(root_handle))
        for index, part in enumerate(relative.parts):
            current = current / part
            directory = index < len(relative.parts) - 1
            handle = api.open(current, directory)
            handles.append(handle)
            attributes = api.attributes(handle)
            require(not attributes & REPARSE_POINT and bool(attributes & DIRECTORY) == directory,
                    'INVALID_PATH', 'Путь изменился или содержит reparse point.')
            require(_canonical(api.final_path(handle)).is_relative_to(root_path),
                    'INVALID_PATH', 'Открытый объект находится вне корня проекта.')
        descriptor = transfer(handles[-1])
        handles.pop()  # Владение конечным handle передано CRT только после успеха.
        return descriptor
    except OSError as error:
        raise DocumentError('INVALID_PATH', 'Windows не смог безопасно открыть исходный файл.') from error
    finally:
        for handle in reversed(handles):
            api.close(handle)
