"""Bounded Git object reads for the Atlas health CI pilot."""

from dataclasses import dataclass
import os
import re
import signal
import subprocess
from threading import Thread
from time import monotonic
import unicodedata
from pathlib import Path


_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_REGULAR = {b"100644", b"100755"}
MAX_BLOB_BYTES = 1024 * 1024
_MAX_INVENTORY_BYTES = 8 * 1024 * 1024
_GIT_TIMEOUT = 25


class GitInputError(ValueError):
    """A safe error message that never contains Git output or source text."""


@dataclass(frozen=True)
class Change:
    """An immutable raw Git diff entry with optional regular-file objects."""

    status: str
    before_path: str | None
    after_path: str | None
    before_blob: str | None
    after_blob: str | None
    regular: bool


def _bounded_stdout(command, environment, limit):
    """Read at most limit + 1 bytes and stop Git on overflow or deadline."""
    own_process_group = os.name == "posix"
    with subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, start_new_session=own_process_group) as process:
        output = []

        def read_stdout():
            try:
                output.append(process.stdout.read(limit + 1))
            except OSError:
                return

        reader = Thread(target=read_stdout, daemon=True)
        deadline = monotonic() + _GIT_TIMEOUT
        reader.start()
        try:
            reader.join(_GIT_TIMEOUT)
            if reader.is_alive():
                raise subprocess.TimeoutExpired(command, _GIT_TIMEOUT)
            if not output:
                raise OSError("Git output unavailable")
            if len(output[0]) > limit:
                raise GitInputError("Git diff inventory exceeded the 8 MiB budget")
            status = process.wait(timeout=max(0, deadline - monotonic()))
            return subprocess.CompletedProcess(command, status, output[0])
        finally:
            if process.poll() is None or reader.is_alive():
                try:
                    if own_process_group:
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
            process.wait()
            reader.join()


def git(repo: Path, *args: str, output_limit=None) -> bytes:
    """Run Git with replacement objects disabled; never relay its diagnostics."""
    environment = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"):
        environment.pop(key, None)
    command = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(repo), *args]
    try:
        if output_limit is None:
            result = subprocess.run(command, env=environment, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=_GIT_TIMEOUT, check=False)
        else:
            result = _bounded_stdout(command, environment, output_limit)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitInputError(f"Could not run Git {args[0]}") from error
    if result.returncode:
        raise GitInputError(f"Git {args[0]} failed; check the repository and required commit objects")
    return result.stdout


def commit(repo: Path, value: str) -> str:
    """Resolve only exact commit object IDs, including SHA-256 repositories."""
    oid = value.lower()
    if not _OBJECT_ID.fullmatch(oid):
        raise GitInputError("Base and head must be full Git commit IDs")
    resolved = git(repo, "rev-parse", "--verify", "--end-of-options", f"{oid}^{{commit}}").strip()
    if resolved != oid.encode("ascii"):
        raise GitInputError("Base and head must identify commit objects directly")
    return oid


def _path(raw: bytes) -> str | None:
    """Reject unsafe, ambiguous or non-UTF-8 locations before producing CI annotations."""
    try:
        value = raw.decode("utf-8")
    except UnicodeError:
        return None
    if not value or len(value) > 1024 or "\\" in value:
        return None
    if any(part in {"", ".", ".."} for part in value.split("/")):
        return None
    if any(unicodedata.category(char).startswith("C") for char in value):
        return None
    return value


def changes(repo: Path, base: str, head: str):
    """Yield file changes without inspecting the mutable worktree or opening paths."""
    data = git(
        repo, "diff", "--raw", "-z", "--no-abbrev", "--no-ext-diff", "--no-textconv", "--no-color",
        "--no-relative", "--ignore-submodules=none", "--find-renames=50%", "-l512",
        "--diff-filter=AMDRT", base, head, "--", output_limit=_MAX_INVENTORY_BYTES,
    )
    entries = iter(data.split(b"\0"))
    for header in entries:
        if not header:
            break
        old_mode, new_mode, old_oid, new_oid, status = header.removeprefix(b":").split()
        kind = status[:1].decode("ascii")
        first = _path(next(entries))
        second = _path(next(entries)) if kind == "R" else first
        before_path = first if kind != "A" else None
        after_path = second if kind != "D" else None
        regular = (kind == "A" or old_mode in _REGULAR) and (kind == "D" or new_mode in _REGULAR)
        yield Change(kind, before_path, after_path,
                     old_oid.decode("ascii") if kind != "A" and regular else None,
                     new_oid.decode("ascii") if kind != "D" and regular else None, regular)


def blob_size(repo: Path, oid: str | None) -> int:
    """Read the object size before requesting its bytes."""
    return int(git(repo, "cat-file", "-s", oid)) if oid else 0


def read_blob(repo: Path, oid: str | None) -> bytes:
    """Read a previously size-checked immutable blob."""
    return git(repo, "cat-file", "blob", oid) if oid else b""


def line_ranges(repo: Path, change: Change, before: bytes, after: bytes):
    """Return inclusive ranges of removed and added Git diff lines."""
    if not change.before_blob or not change.after_blob:
        content = before if change.before_blob else after
        count = content.count(b"\n") + bool(content and not content.endswith(b"\n"))
        ranges = [(1, count)] if count else []
        return (ranges, []) if change.before_blob else ([], ranges)
    if change.before_blob == change.after_blob:
        return [], []
    patch = git(
        repo, "diff", "--no-ext-diff", "--no-textconv", "--text", "--no-color",
        "--no-indent-heuristic", "--diff-algorithm=myers", "--unified=0",
        "--inter-hunk-context=0", "--word-diff=none",
        "--output-indicator-new=+", "--output-indicator-old=-", "--output-indicator-context= ",
        change.before_blob, change.after_blob, "--",
    )
    left, right = [], []
    for line in patch.split(b"\n"):
        match = _HUNK.match(line)
        if match:
            for ranges, start, count in ((left, *match.groups()[:2]), (right, *match.groups()[2:])):
                length = int(count) if count is not None else 1
                if length:
                    first = int(start)
                    ranges.append((first, first + length - 1))
    return left, right
