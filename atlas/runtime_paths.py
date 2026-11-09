"""Постоянные пользовательские данные отделены от обновляемого приложения."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from .process_lock import ProcessLock
from .backup_retention import checked_backup_directory, prune_automatic_backups

APP_VERSION = "0.1.6"
DATA_SCHEMA_VERSION = 1


def worker_executable() -> str:
    """Windows GUI не имеет stdio; анализаторы и MCP используют соседний console runtime."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False) and executable.name.lower() == "codeatlasdesktop.exe":
        return str(executable.with_name("CodeAtlas.exe"))
    return sys.executable


def worker_creation_flags() -> int:
    """Windows анализаторы сохраняют pipes, но не создают всплывающие console окна."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def resource_root() -> Path:
    """Возвращает каталог ресурсов исходного проекта или frozen onedir-бандла."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def default_data_dir(platform: str | None = None, environ: dict | None = None, home: Path | None = None) -> Path:
    """Выбирает стабильный per-user путь, уважая явное ATLAS_DATA_DIR."""
    platform = platform or sys.platform
    environment = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if environment.get("ATLAS_DATA_DIR"):
        return Path(environment["ATLAS_DATA_DIR"]).expanduser().absolute()
    if platform == "darwin":
        default = home / "Library" / "Application Support" / "Code Atlas"
    elif platform == "win32":
        default = Path(environment.get("LOCALAPPDATA") or home / "AppData" / "Local") / "Code Atlas"
    else:
        base = Path(environment.get("XDG_DATA_HOME") or home / ".local" / "share")
        if not base.is_absolute():
            base = home / ".local" / "share"
        default = base / "code-atlas"
    from desktop_support import read_installation
    return read_installation("code-atlas", default, platform=platform, environ=environment, home=home).data_directory


def managed_repositories_dir(data_dir: Path) -> Path:
    """Отделяет управляемые Git-клоны от каталога снимков, который запрещено индексировать."""
    path = Path(data_dir)
    return path.parent / (path.name + "-repositories")


def ensure_data_schema(data_dir: Path) -> None:
    """Создаёт marker версии и запрещает старому приложению открывать будущую схему."""
    path = Path(data_dir) / "data-schema.json"
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))["schemaVersion"]
            if not isinstance(value, int) or isinstance(value, bool) or value != DATA_SCHEMA_VERSION:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError("Версия каталога данных не поддерживается. Не удаляйте данные; используйте совместимую версию Atlas.") from None
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps({"schemaVersion": DATA_SCHEMA_VERSION, "applicationVersion": APP_VERSION}), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _rewrite_snapshot_paths(value, source: Path, target: Path) -> None:
    """Переносит только служебные ссылки снимков; sourceproject paths/заметки не меняются."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                if key in {"_graph", "_rawGraph", "_mcpGraph", "rawGraphPath"} and isinstance(item, str):
                    try:
                        relative = Path(item).relative_to(source)
                    except ValueError:
                        continue
                    current[key] = str(target / relative)
                elif isinstance(item, (dict, list)):
                    pending.append(item)
        elif isinstance(current, list):
            pending.extend(current)


def migrate_legacy_data(source: Path, target: Path) -> dict:
    """Копирует весь старый каталог атомарно; не перезаписывает target и не удаляет source."""
    source, target = Path(source).resolve(), Path(target).absolute()
    if not source.is_dir() or target.exists() or target == source or source in target.parents:
        raise RuntimeError("Перенос требует существующий источник и новый отдельный каталог назначения.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with ProcessLock(source / "server.lock"):
        for path in source.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("Автоперенос каталога с символическими ссылками не поддерживается; исходные данные не изменены.")
        staging = Path(tempfile.mkdtemp(prefix=".code-atlas-migration-", dir=target.parent))
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns("server.lock"))
            rewritten = 0
            for path in sorted(staging.rglob("*.json")):
                # Непонятные JSON-файлы сохраняем байт-в-байт: пользовательская
                # база знаний не обязана иметь структуру внутренних снимков.
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeError):
                    continue
                previous = json.dumps(value, ensure_ascii=False)
                _rewrite_snapshot_paths(value, source, target)
                payload = json.dumps(value, ensure_ascii=False)
                if payload != previous:
                    path.write_text(payload, encoding="utf-8")
                    rewritten += 1
            ensure_data_schema(staging)
            marker = {"source": str(source), "createdAt": datetime.now(timezone.utc).isoformat(), "rewrittenJsonFiles": rewritten}
            (staging / "migration.json").write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
            if target.exists():
                raise RuntimeError("Каталог назначения появился во время переноса; он не изменён.")
            staging.rename(target)
            return marker
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def backup_data(data_dir: Path) -> Path:
    """Публикует полный ZIP под блокировкой, затем оставляет три последних автоматических ZIP."""
    data_dir = Path(data_dir).resolve()
    if not data_dir.is_dir():
        raise RuntimeError("Каталог данных для резервной копии не найден.")
    from desktop_support import configured_backup_dir
    destination = checked_backup_directory(configured_backup_dir("code-atlas", data_dir))
    destination.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".zip"
    target = destination / name
    temporary = target.with_suffix(".tmp")
    with ProcessLock(data_dir / "server.lock"):
        try:
            with ZipFile(temporary, "x", compression=ZIP_DEFLATED) as archive:
                for path in sorted(data_dir.rglob("*")):
                    if path.is_symlink():
                        raise RuntimeError("Резервная копия с символическими ссылками требует ручного переноса.")
                    if path.is_file() and path.name != "server.lock":
                        archive.write(path, path.relative_to(data_dir).as_posix())
            temporary.rename(target)
            # Только опубликованная копия позволяет удалить более старые автоматические ZIP.
            prune_automatic_backups(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return target
