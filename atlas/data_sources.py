"""Обнаружение по именам и безопасный захват только явно выбранных файлов."""

from hashlib import sha256
import os
from pathlib import Path, PurePosixPath

from .data_models import DataError, MAX_INPUT_BYTES, require

EXTENSIONS = {".csv", ".json", ".jsonl", ".ndjson", ".feature"}
SKIP_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "bin", "obj", "__pycache__", ".code-atlas", "dist", "build"}


def storage_bytes(directory):
    """Считает доступные обычные файлы, терпимо к публикации/удалению staging."""
    total = 0
    if directory is None:
        return total
    for path in Path(directory).rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def source_path(root: Path, relative: str) -> Path:
    """Разрешает только обычный файл внутри корня, исключая symlink-компоненты."""
    token = PurePosixPath(relative)
    require(bool(relative) and len(relative) <= 4096 and not token.is_absolute()
            and ".." not in token.parts and "\\" not in relative, "INVALID_PATH", "Нужен относительный путь внутри выбранного каталога.")
    path = root
    for part in token.parts:
        path = path / part
        require(not path.is_symlink(), "INVALID_PATH", "Символические ссылки не импортируются.")
    require(path.is_file() and path.resolve().is_relative_to(root), "SOURCE_MISSING", "Выбранный файл отсутствует или вышел за корень.", 409)
    require(path.suffix.lower() in EXTENSIONS, "INVALID_FORMAT", "Поддерживаются CSV, JSON/JSONL и Gherkin .feature.")
    return path


def discover(root: Path, excluded: Path, limit: int = 1000) -> dict:
    """Читает только имена/stat; ни содержимое, ни индексы не строятся."""
    files, visited, directories, truncated = [], 0, 0, False
    for directory, names, filenames in os.walk(root, followlinks=False):
        directories += 1
        if directories > 10000:
            truncated = True
            break
        current = Path(directory)
        names[:] = sorted(name for name in names if name not in SKIP_DIRECTORIES
                          and not (current / name).is_symlink()
                          and not (current / name).resolve().is_relative_to(excluded))
        for name in sorted(filenames):
            visited += 1
            if visited > 50_000 or len(files) >= limit:
                truncated = True
                break
            path = current / name
            if path.suffix.lower() not in EXTENSIONS or path.is_symlink() or path.resolve().is_relative_to(excluded):
                continue
            try:
                files.append({"path": path.relative_to(root).as_posix(), "sizeBytes": path.stat().st_size,
                              "format": "gherkin" if path.suffix.lower() == ".feature" else path.suffix.lower()[1:]})
            except OSError:
                continue
        if truncated:
            break
    return {"files": files, "truncated": truncated, "scannedFiles": visited, "built": False}


def fingerprint(path: Path) -> list[int]:
    """Stamp обнаруживает обычную замену/редактуру, включая сохранённый mtime."""
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]


def digest(path: Path, cancelled=lambda: False) -> str:
    """Хеширует потоково; отмена не требует дочитывания гигабайтного файла."""
    result = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            require(not cancelled(), "CANCELLED", "Импорт отменён.", 409)
            result.update(block)
    return result.hexdigest()


def capture(root: Path, files: list[str], target: Path, progress, cancelled) -> list[dict]:
    """Копирует снимок с хешем и повторной проверкой источника до публикации."""
    paths = [source_path(root, name) for name in files]
    require(sum(path.stat().st_size for path in paths) <= MAX_INPUT_BYTES, "INPUT_LIMIT", "Суммарный вход превышает 1 ГБ.")
    results, total = [], 0
    target.mkdir(parents=True, exist_ok=True)
    for number, (relative, path) in enumerate(zip(files, paths)):
        before, checksum = fingerprint(path), sha256()
        destination = target / f"{number}{path.suffix.lower()}"
        with path.open("rb") as incoming, destination.open("xb") as outgoing:
            while block := incoming.read(1024 * 1024):
                require(not cancelled(), "CANCELLED", "Импорт отменён.", 409)
                total += len(block)
                require(total <= MAX_INPUT_BYTES, "INPUT_LIMIT", "Файлы выросли сверх общего лимита 1 ГБ.")
                outgoing.write(block)
                checksum.update(block)
                progress(bytesRead=total, file=relative, phase="capture")
        require(before == fingerprint(path) and digest(path, cancelled) == checksum.hexdigest(),
                "SOURCE_CHANGED", "Исходник изменился во время захвата. Повторите импорт после сохранения файлов.", 409)
        results.append({"path": relative, "captured": destination.name, "sha256": checksum.hexdigest(),
                        "stamp": before, "sizeBytes": destination.stat().st_size})
    return results
