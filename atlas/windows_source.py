"""Чтение Windows-файла после проверки конечного пути открытого HANDLE."""

import ctypes
from ctypes import wintypes
import os


def read_windows_file(root, relative, maximum: int) -> bytes:
    """Проверяет открытый объект до чтения; junction не позволяет выйти из корня.

    HANDLE закрепляет файл на время проверки и чтения. FinalPath возвращает
    разрешённый ОС путь, включая промежуточные junction; сравнение строк пути
    до открытия не обеспечило бы эту гарантию при переименовании каталогов.
    """
    from .source_viewer import SourceError
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final_path.restype = wintypes.DWORD
    file_type = kernel.GetFileType
    file_type.argtypes = [wintypes.HANDLE]
    file_type.restype = wintypes.DWORD
    size_of = kernel.GetFileSizeEx
    size_of.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    size_of.restype = wintypes.BOOL
    read = kernel.ReadFile
    read.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    read.restype = wintypes.BOOL
    attributes = kernel.GetFileInformationByHandleEx
    attributes.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    attributes.restype = wintypes.BOOL
    # BACKUP_SEMANTICS позволяет распознать каталог и вернуть точную ошибку 415.
    handle = create(str(root / relative), 0x80000000, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise SourceError("Исходный файл отсутствует или недоступен.", 404)
    try:
        if file_type(handle) != 1:
            raise SourceError("Просмотр доступен только для обычных файлов.", 415)
        tags = (wintypes.DWORD * 2)()
        if not attributes(handle, 9, ctypes.byref(tags), ctypes.sizeof(tags)):
            raise SourceError("Не удалось проверить тип исходного файла.", 403)
        if tags[0] & 0x400:
            raise SourceError("Путь исходного файла изменился или недопустим.", 403)
        if tags[0] & 0x10:
            raise SourceError("Просмотр доступен только для обычных файлов.", 415)
        buffer = ctypes.create_unicode_buffer(32768)
        length = final_path(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise SourceError("Не удалось проверить путь исходного файла.", 403)
        actual = buffer.value
        if actual.startswith("\\\\?\\UNC\\"):
            actual = "\\\\" + actual[8:]
        elif actual.startswith("\\\\?\\"):
            actual = actual[4:]
        root_path = os.path.normcase(os.path.normpath(str(root)))
        actual = os.path.normcase(os.path.normpath(actual))
        try:
            contained = os.path.commonpath([root_path, actual]) == root_path and actual != root_path
        except ValueError:
            contained = False
        if not contained:
            raise SourceError("Чтение файлов за пределами выбранного проекта запрещено.", 403)
        size = ctypes.c_longlong()
        if not size_of(handle, ctypes.byref(size)):
            raise SourceError("Исходный файл недоступен.", 404)
        if size.value > maximum:
            raise SourceError("Файл превышает ограничение просмотрщика: 1 МиБ.", 413)
        content = bytearray()
        while len(content) <= maximum:
            chunk = ctypes.create_string_buffer(min(65536, maximum + 1 - len(content)))
            received = wintypes.DWORD()
            if not read(handle, chunk, len(chunk), ctypes.byref(received), None):
                raise SourceError("Не удалось прочитать исходный файл.", 404)
            if not received.value:
                return bytes(content)
            content.extend(chunk.raw[:received.value])
        raise SourceError("Файл превышает ограничение просмотрщика: 1 МиБ.", 413)
    finally:
        close(handle)
