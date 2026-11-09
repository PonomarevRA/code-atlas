"""Ограниченное чтение исходного файла узла из текущей рабочей директории."""

import errno
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
import stat


MAX_SOURCE_BYTES = 1024 * 1024


class SourceError(RuntimeError):
    """Ожидаемая ошибка чтения без раскрытия посторонних путей."""

    def __init__(self, message: str, status: int = 400):
        """Сохраняет понятное сообщение и рекомендуемый HTTP-статус."""
        super().__init__(message)
        self.status = status


def _source_path(project_path: Path, node: dict) -> tuple[Path, Path]:
    """Разрешает путь из снимка и допускает symlink только внутри корня проекта."""
    source = node.get("file") or node.get("source_file")
    if not isinstance(source, str) or not source or node.get("kind") == "external":
        raise SourceError("У этого узла нет доступного исходного файла.", 404)
    try:
        root = project_path.resolve(strict=True)
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise SourceError("Исходный файл отсутствует или недоступен.", 404) from exc
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise SourceError("Чтение файлов за пределами выбранного проекта запрещено.", 403) from exc
    if not relative.parts:
        raise SourceError("Для этого узла указан каталог, а не исходный файл.", 415)
    return root, relative


def _read_regular_file(root: Path, relative: Path, max_bytes: int = MAX_SOURCE_BYTES) -> bytes:
    """Открывает путь без symlink с заданным внутренним бюджетом; исходник ограничен 1 МиБ.

    Проверка каждого сегмента через dir_fd не позволяет подменить промежуточный
    каталог ссылкой после проверки границ. O_NONBLOCK исключает зависание на FIFO.
    """
    if os.name == "nt":
        from .windows_source import read_windows_file
        return read_windows_file(root, relative, max_bytes)
    directory_fd = None
    file_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_fd = os.open(root, flags)
        for segment in relative.parts[:-1]:
            next_fd = os.open(segment, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            relative.parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise SourceError("Просмотр доступен только для обычных текстовых файлов.", 415)
        size_message = "Файл превышает ограничение просмотрщика: 1 МиБ." if max_bytes == MAX_SOURCE_BYTES else f"Файл превышает ограничение чтения: {max_bytes} байт."
        if info.st_size > max_bytes:
            raise SourceError(size_message, 413)
        content = bytearray()
        while len(content) <= max_bytes:
            chunk = os.read(file_fd, min(65536, max_bytes + 1 - len(content)))
            if not chunk:
                return bytes(content)
            content.extend(chunk)
        raise SourceError(size_message, 413)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise SourceError("Путь исходного файла изменился или недопустим.", 403) from exc
        raise SourceError("Исходный файл отсутствует или недоступен.", 404) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def read_node_source(project_path: Path, node: dict, snapshot_id: str, *, include_syntax: bool = True) -> dict:
    """Возвращает текущий UTF-8-файл и позицию из снимка, не обещая совпадение версий.

    Сборщики фактов могут отключить необязательную подсветку: проверка границ,
    кодировки и хеш исходных байтов остаются такими же, как у просмотрщика.
    """
    root, relative = _source_path(project_path, node)
    content = _read_regular_file(root, relative)
    source_read_at = datetime.now(timezone.utc).isoformat()
    if any(byte < 32 and byte not in {9, 10, 12, 13} for byte in content):
        raise SourceError("Бинарные файлы не поддерживаются просмотрщиком исходников.", 415)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceError("Файл имеет неподдерживаемую кодировку. Ожидается UTF-8.", 415) from exc
    line = node.get("line")
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        line = None
    # Как в редакторе: пустой файл содержит одну видимую строку, завершающий
    # перевод строки создаёт последнюю пустую строку. Сам текст не меняем.
    normalized_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    total_lines = normalized_newlines.count("\n") + 1
    result = {
        "text": text, "file": relative.as_posix(), "line": line,
        "totalLines": total_lines,
        "snapshotId": snapshot_id,
        "sourceKind": "working-tree",
        "sourceByteHash": hashlib.sha256(content).hexdigest(),
        "sourceReadAt": source_read_at,
        "warning": "Показан текущий файл рабочей директории. Строка взята из сохранённого снимка и могла сместиться после изменения файла.",
    }
    if include_syntax:
        from .source_syntax import highlight_source
        result['syntax'] = highlight_source(relative.as_posix(), text)
    return result
