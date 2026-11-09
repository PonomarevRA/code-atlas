"""Сравнение локальных коммитов с подсветкой файлов сохранённого графа."""

from collections import defaultdict
import os
from pathlib import Path, PurePosixPath
from queue import Empty, Full, Queue
from threading import Event, Thread
import subprocess
import time
import unicodedata


class GitCompareError(RuntimeError):
    """Ошибка сравнения с HTTP-статусом для слоя API."""

    def __init__(self, message: str, status: int = 400):
        """Сохраняет понятное сообщение и рекомендуемый статус ответа."""
        super().__init__(message)
        self.status = status


def _environment() -> dict[str, str]:
    """Исключает переопределения репозитория и запрещает автоматическую загрузку Git."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({
        "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    })
    return environment


def _read_output(process, deadline: float) -> dict[str, bytearray]:
    """Читает обе трубы потоками: Windows selector не поддерживает pipe-дескрипторы.

    Ограниченная очередь сохраняет лимит памяти даже при быстром stderr. При
    ошибке читатели завершаются после остановки процесса вызывающим кодом.
    """
    queue = Queue(maxsize=8)
    stop = Event()
    chunks = {"stdout": bytearray(), "stderr": bytearray()}

    def read_stream(name, stream):
        """Передаёт ограниченные порции и EOF; не удерживает неограниченный вывод."""
        try:
            while not stop.is_set():
                block = stream.read1(65536)
                while not stop.is_set():
                    try:
                        queue.put((name, block, None), timeout=0.1)
                        break
                    except Full:
                        continue
                if not block:
                    return
        except OSError as error:
            if not stop.is_set():
                try:
                    queue.put((name, b"", error), timeout=0.1)
                except Full:
                    pass

    readers = [Thread(target=read_stream, args=(name, getattr(process, name)), daemon=True)
               for name in chunks]
    for reader in readers:
        reader.start()
    finished = 0
    try:
        while finished < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GitCompareError("Сравнение Git превысило лимит 30 секунд.", 504)
            try:
                name, block, error = queue.get(timeout=min(remaining, 0.5))
            except Empty:
                continue
            if error:
                raise error
            if not block:
                finished += 1
                continue
            chunks[name].extend(block)
            limit = 4 * 1024 * 1024 if name == "stdout" else 128 * 1024
            if len(chunks[name]) > limit:
                raise GitCompareError("Слишком большой список изменений Git.", 413)
        return chunks
    finally:
        stop.set()


def _git(cwd: Path, arguments: list[str]) -> bytes:
    """Читает вывод Git с лимитом 30 секунд и 4 МиБ, прерывая слишком большой ответ."""
    command = [
        "git", "--no-optional-locks", "-c", "core.fsmonitor=false",
        "-c", "protocol.allow=never", *arguments,
    ]
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        with subprocess.Popen(
            command, cwd=cwd, env=_environment(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            try:
                deadline = time.monotonic() + 30
                chunks = _read_output(process, deadline)
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except BaseException:
                process.kill()
                process.wait()
                raise
    except subprocess.TimeoutExpired as exc:
        raise GitCompareError("Сравнение Git превысило лимит 30 секунд.", 504) from exc
    except OSError as exc:
        raise GitCompareError(f"Не удалось запустить локальный Git: {exc}", 503) from exc
    if process.returncode:
        detail = chunks["stderr"].decode("utf-8", errors="replace").strip()[-1000:]
        raise GitCompareError(f"Git не смог выполнить сравнение: {detail}")
    return bytes(chunks["stdout"])


def _validate_ref(reference: str) -> str:
    """Отсекает пустые, слишком длинные ссылки, управляющие символы и опции CLI."""
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 512:
        raise GitCompareError("Укажите Git-ссылку длиной от 1 до 512 символов.")
    if reference.startswith("-") or any(unicodedata.category(char) == "Cc" for char in reference):
        raise GitCompareError("Git-ссылка содержит недопустимые символы или начинается с '-'.")
    return reference


def _commit(root: Path, reference: str) -> str:
    """Разрешает локальную ссылку строго в commit, без checkout и загрузки объектов."""
    result = _git(root, ["rev-parse", "--verify", "--end-of-options", reference + "^{commit}"])
    return result.decode("ascii").strip()


def _changed_files(output: bytes) -> list[dict]:
    """Разбирает NUL-разделённые статусы и пути, включая два имени переименования."""
    if not output:
        return []
    fields = output.split(b"\0")
    if fields[-1] != b"":
        raise GitCompareError("Git вернул незавершённый список изменений.", 502)
    fields.pop()
    index = 0
    files = []
    while index < len(fields):
        status = fields[index].decode("ascii", errors="replace")
        index += 1
        path_count = 2 if status.startswith(("R", "C")) else 1
        if not status or status[0] not in "ACDMRTUXB" or index + path_count > len(fields):
            raise GitCompareError("Git вернул некорректный формат изменений.", 502)
        paths = [os.fsdecode(value) for value in fields[index:index + path_count]]
        index += path_count
        item = {"status": status, "path": paths[-1], "nodeIds": []}
        if path_count == 2:
            item["oldPath"] = paths[0]
        files.append(item)
    return files


def _in_scope(path: str, prefix: PurePosixPath) -> bool:
    """Проверяет границу каталога по сегментам, исключая соседние похожие имена."""
    try:
        PurePosixPath(path).relative_to(prefix)
        return True
    except ValueError:
        return False


def _node_files(project: Path, root: Path, graph: dict) -> dict[str, set[str]]:
    """Сопоставляет файлы snapshot с путями Git; файлы не обязаны существовать сейчас."""
    matches = defaultdict(set)
    for node in graph.get("nodes", []):
        source = node.get("file") or node.get("source_file")
        if not source or node.get("kind") == "external":
            continue
        path = Path(source)
        if not path.is_absolute():
            path = project / path
        # Лексическая нормализация сохраняет удалённые пути и не следует symlink.
        path = Path(os.path.normpath(path))
        try:
            path.relative_to(project)
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        matches[relative].add(node["id"])
    return matches


def compare_graph(project_path: Path, graph: dict, base_ref: str, target_ref: str, mode: str = "direct") -> dict:
    """Сравнивает коммиты и связывает изменённые файлы с узлами сохранённого снимка.

    Пути результата относительны корню Git. Для индекса подкаталога учитываются
    только изменения внутри него, включая переименования через его границу.
    Подсветка имеет файловую точность, не доказывает изменение каждого символа.
    """
    base_ref, target_ref = _validate_ref(base_ref), _validate_ref(target_ref)
    if mode not in {"direct", "merge-base"}:
        raise GitCompareError("Поддерживаются режимы direct и merge-base.")
    project = project_path.expanduser().resolve()
    root = Path(os.fsdecode(_git(project, ["rev-parse", "--show-toplevel"])).rstrip("\n")).resolve()
    try:
        prefix = PurePosixPath(project.relative_to(root).as_posix())
    except ValueError as exc:
        raise GitCompareError("Каталог проекта находится вне найденного Git-репозитория.") from exc
    base_commit, target_commit = _commit(root, base_ref), _commit(root, target_ref)
    comparison_base = base_commit
    if mode == "merge-base":
        comparison_base = _git(root, ["merge-base", base_commit, target_commit]).decode("ascii").strip()
    output = _git(root, [
        "diff", "--name-status", "-z", "-M", "--no-ext-diff", "--no-textconv",
        "--submodule=short", base_commit if mode == "direct" else comparison_base, target_commit, "--",
    ])
    matches = _node_files(project, root, graph)
    files = []
    changed = set()
    for item in _changed_files(output):
        paths = [item["path"]]
        if "oldPath" in item:
            paths.append(item["oldPath"])
        if not any(_in_scope(path, prefix) for path in paths):
            continue
        identifiers = set()
        for path in paths:
            identifiers.update(matches.get(path, set()))
        item["nodeIds"] = sorted(identifiers)
        changed.update(identifiers)
        files.append(item)
    files.sort(key=lambda item: (item["path"], item.get("oldPath", ""), item["status"]))
    return {
        "baseRef": base_ref, "targetRef": target_ref,
        "baseCommit": base_commit, "targetCommit": target_commit,
        "comparisonBase": comparison_base, "mode": mode, "precision": "file",
        "files": files, "changedNodeIds": sorted(changed),
        "unmatchedFileCount": sum(not item["nodeIds"] for item in files),
        "totalChangedFiles": len(files), "matchedNodeCount": len(changed),
        "warning": "Подсвечены все узлы изменённых файлов сохранённого снимка. Снимок может отличаться от сравниваемых коммитов; изменения отдельных символов и строк не определялись.",
    }
