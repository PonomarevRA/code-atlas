"""Небольшие immutable оценки/планы отделены от полных графов и исходников."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from uuid import uuid4

from .code_health_policy import policy


class HealthError(RuntimeError):
    """Ожидаемая ошибка запроса; отсутствие исходной оценки не является freshness ошибкой."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class HealthRecords:
    """Хранит ограниченное число маленьких оценок на проект, без чернового текста."""
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "code-health"
        self.lock = threading.RLock()

    def directory(self, project_id: str) -> Path:
        """Пользовательский ID никогда не становится файловым путём."""
        return self.root / hashlib.sha256(project_id.encode()).hexdigest()[:24]

    def save(self, project_id: str, value: dict, kind: str = "assessment") -> dict:
        """Атомарно сохраняет новую запись и вытесняет старые оценки по явному лимиту."""
        key = "planId" if kind == "plan" else "assessmentId"
        value = {**value, key: uuid4().hex, "recordKind": kind, "projectId": project_id,
                 "createdAt": datetime.now(timezone.utc).isoformat()}
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(payload) > policy()["limits"]["maxRecordBytes"]:
            raise HealthError("Health result is too large; narrow the selection", 413)
        with self.lock:
            directory = self.directory(project_id)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / (value[key] + ".json")
            temporary = target.with_suffix(".tmp")
            try:
                temporary.write_bytes(payload)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            old = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
            for path in old[:-policy()["limits"]["maxRecords"]]:
                path.unlink(missing_ok=True)
        return value

    def read(self, project_id: str, identifier: str, kind: str = "assessment") -> dict | None:
        """Отсутствующая/истёкшая оценка возвращает None; чужая запись не раскрывается."""
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise HealthError("Invalid health record ID")
        path = self.directory(project_id) / (identifier + ".json")
        with self.lock:
            if not path.is_file() or path.is_symlink():
                return None
            if path.stat().st_size > policy()["limits"]["maxRecordBytes"]:
                raise HealthError("Invalid health record size", 413)
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
        return value if value.get("projectId") == project_id and value.get("recordKind") == kind else None
