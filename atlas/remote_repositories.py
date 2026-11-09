"""Ограниченные фоновые Git clone без checkout-фильтров и выполнения исходников."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from threading import Event, RLock
from urllib.parse import urlsplit
from uuid import uuid4

from .store import StoreError


class RemoteRepositoryError(StoreError):
    """Безопасная ошибка без URL, stderr или реквизитов Git."""


def validate_remote(url: str, ref: str = "") -> tuple[str, str]:
    """Разрешает HTTPS/SSH без паролей; ref — имя ветки или тега, не выражение Git."""
    if not isinstance(url, str) or not url or len(url) > 2048 or any(char.isspace() or ord(char) < 32 for char in url):
        raise RemoteRepositoryError("Укажите HTTPS или SSH URL репозитория.")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise RemoteRepositoryError("Некорректный URL репозитория.") from None
    if parsed.scheme in {"https", "ssh"}:
        try:
            host = parsed.hostname or ""
            safe_host = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host) or re.fullmatch(r"[0-9A-Fa-f:]+", host)
            valid = bool(safe_host and parsed.path and parsed.port != 0)
        except ValueError:
            valid = False
        if parsed.query or parsed.fragment or parsed.password or (parsed.scheme == "https" and parsed.username):
            valid = False
        if parsed.scheme == "ssh" and parsed.username and not re.fullmatch(r"[A-Za-z0-9_.-]+", parsed.username):
            valid = False
    else:
        valid = bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*:[A-Za-z0-9_./-]+", url))
    if not valid:
        raise RemoteRepositoryError("Допустимы HTTPS без логина/токена или SSH URL. Используйте Git credential manager либо SSH agent.")
    if len(ref) > 200 or (ref and (ref.startswith(("-", "/")) or ref.endswith((".", "/", ".lock")) or ".." in ref or "@{" in ref or any(char.isspace() or ord(char) < 32 or char in '~^:?*[\\' for char in ref))):
        raise RemoteRepositoryError("Укажите имя ветки или тега без выражений и параметров Git.")
    return url, ref


def extract_archive(archive: Path, destination: Path) -> dict:
    """Материализует только обычные файлы; ссылки, submodule и специальные записи не создаёт."""
    skipped = 0
    files = 0
    total = 0
    with tarfile.open(archive, mode="r|") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or any(part.casefold() == ".git" or ":" in part or part.endswith((".", " ")) for part in path.parts) or "\\" in member.name:
                raise RemoteRepositoryError("Git-архив содержит небезопасный путь.")
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise RemoteRepositoryError("Git-архив выходит за границы локальной копии.")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                skipped += 1
                continue
            files += 1
            total += member.size
            if files > 100_000 or total > 512 * 1024 * 1024 or member.size > 64 * 1024 * 1024:
                raise RemoteRepositoryError("Репозиторий превышает лимит: 100000 файлов, 512 МиБ суммарно или 64 МиБ на файл.")
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.extractfile(member)
            if content is None:
                raise RemoteRepositoryError("Не удалось прочитать файл Git-архива.")
            with content, target.open("xb") as output:
                shutil.copyfileobj(content, output)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return {"files": files, "skippedEntries": skipped}


class RemoteRepositories:
    """Один clone worker и ограниченный журнал; индекс запускается через существующий store."""
    def __init__(self, store, root: Path, runner=subprocess.run):
        self.store = store
        self.root = root.resolve()
        if self.root == store.data_dir or store.data_dir in self.root.parents:
            raise ValueError("Remote clone root must be outside index storage")
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.lock = RLock()
        self.stop = Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="atlas-clone")
        self.jobs = {}
        self.active = None

    def submit(self, url: str, ref: str = "") -> dict:
        """Проверяет вход до постановки; не хранит URL с credentials или вывод Git."""
        url, ref = validate_remote(url, ref)
        with self.lock:
            if self.stop.is_set():
                raise RemoteRepositoryError("Atlas завершает работу.", 503)
            if self.active:
                raise RemoteRepositoryError("Дождитесь завершения получения текущего репозитория.", 409)
            if self.store.list_projects().get("activeJob"):
                raise RemoteRepositoryError("Дождитесь завершения индексации.", 409)
            job_id = uuid4().hex
            self.jobs[job_id] = {"id": job_id, "status": "queued", "ref": ref or "default"}
            self.active = job_id
            while len(self.jobs) > 50:
                del self.jobs[next(iter(self.jobs))]
            self.executor.submit(self._work, job_id, url, ref)
            return {"job": deepcopy(self.jobs[job_id])}

    def get(self, job_id: str) -> dict:
        """Возвращает независимую копию состояния фоновой операции."""
        with self.lock:
            if job_id not in self.jobs:
                raise RemoteRepositoryError("Задача получения репозитория не найдена.", 404)
            return deepcopy(self.jobs[job_id])

    def _update(self, job_id: str, **values):
        """Публикует этап атомарно для HTTP polling."""
        with self.lock:
            self.jobs[job_id].update(values)

    def _git(self, arguments: list[str], output=None):
        """Git использует credential manager/agent; hooks отключены, stderr никогда не возвращается."""
        environment = os.environ.copy()
        environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never", "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oStrictHostKeyChecking=yes"})
        # Унаследованные repo/config overrides не должны перенаправлять служебные операции.
        for name in list(environment):
            if name.startswith(("GIT_CONFIG_", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")):
                environment.pop(name, None)
        command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always", *arguments]
        try:
            with tempfile.TemporaryFile() as errors:
                result = self.runner(command, stdin=subprocess.DEVNULL, stdout=output or subprocess.DEVNULL, stderr=errors, env=environment, timeout=120, check=False)
            if result.returncode:
                raise RemoteRepositoryError("Git не смог получить репозиторий. Проверьте адрес, ветку/тег, сеть и авторизацию Git/SSH.")
        except subprocess.TimeoutExpired:
            raise RemoteRepositoryError("Git превысил время ожидания (120 секунд).") from None
        except OSError:
            raise RemoteRepositoryError("Git недоступен. Установите Git и настройте доступ к репозиторию.") from None

    def _tree_limits(self, destination: Path) -> int:
        """Проверяет размер файлов до создания tar и считает пропущенные submodule gitlinks."""
        with tempfile.TemporaryFile() as output:
            self._git(["-C", str(destination), "ls-tree", "-r", "-l", "-z", "HEAD"], output)
            if output.tell() > 32 * 1024 * 1024:
                raise RemoteRepositoryError("Дерево репозитория превышает допустимый размер.")
            output.seek(0)
            records = output.read().split(b"\0")
        total = 0
        files = 0
        submodules = 0
        for record in records:
            if not record:
                continue
            fields = record.split(b"\t", 1)[0].split()
            if fields[0] == b"160000":
                submodules += 1
                continue
            size = int(fields[3])
            total += size
            files += 1
            if files > 100_000 or total > 512 * 1024 * 1024 or size > 64 * 1024 * 1024:
                raise RemoteRepositoryError("Репозиторий превышает лимит: 100000 файлов, 512 МиБ суммарно или 64 МиБ на файл.")
        return submodules

    def _work(self, job_id: str, url: str, ref: str):
        """Clone → безопасные файлы → существующая индексация; install/build исходников не запускается."""
        owned = self.root / job_id
        repository_name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if repository_name.endswith(".git"):
            repository_name = repository_name[:-4]
        slug = re.sub(r"[^A-Za-z0-9_.-]", "-", repository_name).strip(".-")[:80] or "remote"
        destination = owned / ("repo-" + slug)
        keep = False
        try:
            owned.mkdir()
            self._update(job_id, status="cloning")
            args = ["clone", "--no-checkout", "--depth", "1", "--single-branch", "--no-recurse-submodules", "--template="]
            if ref:
                args.extend(["--branch", ref])
            self._git([*args, "--", url, str(destination)])
            submodules = self._tree_limits(destination)
            archive = owned / "source.tar"
            with archive.open("wb") as output:
                self._git(["-C", str(destination), "archive", "--format=tar", "HEAD"], output)
            coverage = extract_archive(archive, destination)
            coverage["skippedSubmodules"] = submodules
            archive.unlink()
            self._git(["-C", str(destination), "read-tree", "HEAD"])
            keep = True
            self._update(job_id, path=str(destination), coverage=coverage)
            result = self.store.add(str(destination))
            self._update(job_id, status="indexing", projectId=result["project"]["id"], indexJobId=result["job"]["id"])
            while not self.stop.is_set():
                current = self.store.get_job(result["job"]["id"])
                if current["status"] == "completed":
                    self._update(job_id, status="completed")
                    return
                if current["status"] == "failed":
                    raise RemoteRepositoryError("Репозиторий получен, но индексирование не удалось. Локальная копия сохранена; повторите обновление индекса.")
                self.stop.wait(.5)
            raise RemoteRepositoryError("Операция прервана при остановке приложения; локальная копия сохранена.")
        except Exception as error:
            message = str(error) if isinstance(error, StoreError) else "Не удалось подготовить локальную копию репозитория."
            self._update(job_id, status="failed", error=message)
        finally:
            if not keep and owned.exists():
                try:
                    shutil.rmtree(owned)
                except OSError:
                    self._update(job_id, cleanupWarning="Не удалось удалить неполную локальную копию.", path=str(owned))
            with self.lock:
                self.active = None

    def request_stop(self):
        """Signal polling cancellation without waiting for Git or closing project resources."""
        self.stop.set()

    def close(self):
        """Завершает polling перед закрытием store; Git ограничен собственным timeout."""
        self.request_stop()
        self.executor.shutdown(wait=True)
