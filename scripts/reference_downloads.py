"""Ограниченная загрузка по явной команде; TAR/HTML преобразуются в ZIP без extract."""
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request
import zipfile

from atlas.reference_documents import MAX_FILE_BYTES, ReferenceError
from atlas.reference_store import MAX_ARCHIVE_BYTES, MAX_EXPANDED_BYTES, MAX_FILES


def download(url: str, output: Path, maximum: int = MAX_ARCHIVE_BYTES):
    """Скачивает в staging с лимитом; ошибка не оставляет файл, пригодный как cache."""
    temporary = output.with_suffix(output.suffix + ".part")
    try:
        size = 0
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as stream:
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > maximum:
                    raise ReferenceError("Download exceeds size limit")
                stream.write(block)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def tar_to_zip(source: Path, output: Path):
    """Переносит обычные файлы TAR; отклоняет traversal/links и ограничивает expanded bytes."""
    temporary = output.with_suffix(".part")
    try:
        with tarfile.open(source, "r:gz") as archive, zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as package:
            expanded = 0
            for number, entry in enumerate(archive, 1):
                path = PurePosixPath(entry.name)
                expanded += entry.size
                if number > MAX_FILES or expanded > MAX_EXPANDED_BYTES:
                    raise ReferenceError("TAR exceeds expanded limits")
                if path.is_absolute() or ".." in path.parts or "\\" in entry.name or not (entry.isdir() or entry.isfile()):
                    raise ReferenceError("Unsafe TAR entry")
                if entry.isdir():
                    continue
                if entry.size > MAX_FILE_BYTES:
                    raise ReferenceError("TAR file exceeds 8 MiB")
                with archive.extractfile(entry) as stream:
                    package.writestr(entry.name, stream.read(MAX_FILE_BYTES + 1))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def archive_for(source: dict, cache: Path, edition: str) -> Path:
    """Готовит одну immutable cache edition; общие URL заранее дедуплицирует orchestrator."""
    key = source.get("cacheKey", source["language"] + "-" + source["title"].split()[0].lower())
    archive = cache / (key + "-" + edition + ".zip")
    cache.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        return archive
    format_name = source.get("format", "zip")
    if format_name == "zip":
        download(source["url"], archive)
    elif format_name == "tar.gz":
        original = archive.with_suffix(".tar.gz")
        if not original.exists():
            download(source["url"], original)
        tar_to_zip(original, archive)
    elif format_name == "pages":
        temporary = archive.with_suffix(".part")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as package:
                for path, url in source["pages"]:
                    with urllib.request.urlopen(url, timeout=120) as response:
                        raw = response.read(MAX_FILE_BYTES + 1)
                    if len(raw) > MAX_FILE_BYTES:
                        raise ReferenceError("HTML page exceeds 8 MiB")
                    package.writestr(path, raw)
                package.writestr("LICENSE.txt", source["license"] + "\n" + source["sourceUrl"])
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        raise ReferenceError("Unsupported preparation archive format")
    return archive
