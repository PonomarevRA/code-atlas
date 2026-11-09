"""Хранение трёх последних автоматических ZIP без удаления ручных копий."""

from datetime import datetime
from pathlib import Path
import re
import stat
from zipfile import ZipFile

from .process_lock import ProcessLock

DEFAULT_BACKUP_COUNT = 3
_AUTOMATIC_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}\.zip")


def automatic_backup(path: Path) -> bool:
    """Распознаёт штатное имя и реальную дату; каталоги и ссылки не являются копиями."""
    if not _AUTOMATIC_NAME.fullmatch(path.name):
        return False
    try:
        datetime.strptime(path.name[:16], "%Y%m%dT%H%M%SZ")
    except ValueError:
        return False
    return stat.S_ISREG(path.lstat().st_mode)


def checked_backup_directory(directory: Path) -> Path:
    """Запрещает обход ссылок и junction, в том числе в родителях каталога копий."""
    directory = Path(directory).absolute()
    for part in (directory, *directory.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise RuntimeError("Каталог резервных копий не должен проходить через ссылки.")
    return directory


def prune_automatic_backups(directory: Path, keep: int = DEFAULT_BACKUP_COUNT) -> list[Path]:
    """После успешного backup оставляет последние keep ZIP по времени в имени.

    Ручные архивы, каталоги, незавершённые файлы и ссылки сохраняются. Перед
    удалением проверяет центральный каталог сохраняемых ZIP; полный CRC не читает.
    Для копий одной секунды порядок задаётся временем записи, затем именем.
    При ошибке проверки ничего не удаляет; ошибка unlink может оставить лишние копии.
    """
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError("Нужно сохранять хотя бы одну резервную копию.")
    directory = checked_backup_directory(directory)
    checked_backup_directory(directory / ".retention.lock")
    with ProcessLock(directory / ".retention.lock"):
        archives = []
        for path in directory.iterdir():
            if automatic_backup(path):
                archives.append(path)
        archives.sort(key=lambda path: (path.name[:16], path.stat().st_mtime_ns, path.name), reverse=True)
        if len(archives) <= keep:
            return []
        for path in archives[:keep]:
            with ZipFile(path) as archive:
                archive.infolist()
        candidates = [(path, path.lstat()) for path in archives[keep:]]
        removed = []
        for path, previous in candidates:
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or current != previous:
                raise RuntimeError("Резервная копия изменилась во время очистки: " + path.name)
            path.unlink()
            removed.append(path)
        return removed

