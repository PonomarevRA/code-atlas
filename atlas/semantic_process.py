"""Bounded local analyzer processes; commands come only from trusted host adapters."""
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
import time


class SemanticProcessError(RuntimeError):
    """A stable reason code without leaking local command arguments into map status."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProcessResult:
    """Captured bytes are bounded jointly across stdout and stderr."""
    returncode: int
    stdout: bytes
    stderr: bytes


def discover_executable(name: str, *, explicit=None, project_candidates=(), search_path=None):
    """Try explicit, known project locations, then PATH without recursive scans or execution.

    A broken explicit choice is not silently replaced by a different system engine.
    This helper establishes presence only; adapters still probe compatibility/context.
    """
    candidates = [explicit] if explicit else list(project_candidates)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return path.absolute()
    if explicit:
        return None
    found = shutil.which(name, path=search_path)
    return Path(found).absolute() if found else None


def _node_installation_candidates():
    """Return only fixed Node installation paths; never inspect package-manager trees.

    Windows Program Files roots come from the host environment. POSIX paths cover
    conventional local/Homebrew installations without evaluating a login shell.
    """
    if sys.platform == 'win32':
        roots = (os.environ.get('ProgramW6432', 'C:/Program Files'),
                 os.environ.get('ProgramFiles', 'C:/Program Files'))
        return tuple(dict.fromkeys(Path(root) / 'nodejs/node.exe' for root in roots if root))
    return (Path('/usr/local/bin/node'), Path('/opt/homebrew/bin/node'))


def discover_node_executable(*, explicit=None, search_path=None):
    """Find executable Node through an explicit path, PATH, then bounded system paths.

    explicit: an optional absolute host preference; an invalid value never falls
    back. search_path: optional PATH used by the caller's isolated diagnostics.
    Returns a present executable Path or None, without running it. Preserve the
    installation spelling so global TypeScript lookup uses the selected prefix.
    """
    selected = discover_executable('node', explicit=explicit, search_path=search_path)
    if selected is not None or explicit:
        return selected
    for candidate in _node_installation_candidates():
        selected = discover_executable('node', explicit=candidate)
        if selected is not None:
            return selected
    return None


def _stop_process(process, children):
    """Stop our POSIX group and observed Windows descendants, including inherited pipes."""
    import psutil
    for child in reversed(children):
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if os.name != 'nt':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # A restricted host can reject killpg for a group whose leader just
            # exited. Observed descendants were handled above; retain direct kill.
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
    elif process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def run_bounded(command, *, cwd, timeout=3, max_output_bytes=1024 * 1024,
                cancel=None, input_bytes=None, environment=None, memory_bytes=512 * 1024 * 1024):
    """Run an absolute executable with no shell and bounded time/input/output.

    Adapters own safe engine flags and workspace interpretation. This is a process
    lifecycle boundary, not an OS sandbox or a ban on an engine's own filesystem I/O.
    RSS is sampled every 20 ms, so the memory budget is observational with overshoot.
    POSIX owns a fresh process group; Windows descendant cleanup uses psutil and
    needs separate native qualification for engines that detach their children.
    """
    import psutil
    if (not command or not all(isinstance(part, str) for part in command)
            or not Path(command[0]).is_absolute()):
        raise ValueError('Expected an absolute host-owned analyzer command')
    if not 0 < timeout <= 120 or not 0 < max_output_bytes <= 32 * 1024 * 1024:
        raise ValueError('Invalid analyzer process budget')
    if type(memory_bytes) is not int or not 0 < memory_bytes <= 2 * 1024 * 1024 * 1024:
        raise ValueError('Invalid analyzer memory budget')
    if input_bytes is not None and (not isinstance(input_bytes, bytes) or len(input_bytes) > 4 * 1024 * 1024):
        raise ValueError('Analyzer input exceeds its budget')
    cancel = cancel if cancel is not None else Event()
    if cancel.is_set():
        raise SemanticProcessError('cancelled')
    env = dict(os.environ if environment is None else environment)
    # Runtime injection flags are not required to locate an installed analyzer.
    for key in ('PYTHONPATH', 'PYTHONSTARTUP', 'NODE_OPTIONS', 'JAVA_TOOL_OPTIONS',
                '_JAVA_OPTIONS', 'LD_PRELOAD', 'DYLD_INSERT_LIBRARIES'):
        env.pop(key, None)
    env.update({'GOTOOLCHAIN': 'local', 'GOPROXY': 'off', 'GOSUMDB': 'off',
                'DOTNET_SKIP_FIRST_TIME_EXPERIENCE': '1', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1'})
    buffers = [bytearray(), bytearray()]
    output_lock, overflow = Lock(), Event()

    def capture(stream, index):
        """Drain both pipes so an analyzer cannot deadlock against a full stderr pipe."""
        try:
            while chunk := stream.read(4096):
                with output_lock:
                    remaining = max_output_bytes - len(buffers[0]) - len(buffers[1])
                    buffers[index].extend(chunk[:max(remaining, 0)])
                    if len(chunk) > remaining:
                        overflow.set()
        finally:
            stream.close()

    with tempfile.TemporaryFile() as incoming:
        incoming.write(input_bytes or b'')
        incoming.seek(0)
        try:
            process = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=incoming,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=os.name != 'nt',
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as error:
            raise SemanticProcessError('launch-failed') from error
        readers = [Thread(target=capture, args=(stream, index), daemon=True)
                   for index, stream in enumerate((process.stdout, process.stderr))]
        children = {}
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        reason = None
        try:
            observed = psutil.Process(process.pid)
            while process.poll() is None:
                rss = 0
                try:
                    rss = observed.memory_info().rss
                    for child in observed.children(recursive=True):
                        children[child.pid] = child
                        try:
                            rss += child.memory_info().rss
                        except psutil.NoSuchProcess:
                            pass
                except psutil.NoSuchProcess:
                    pass
                except (psutil.AccessDenied, PermissionError) as error:
                    raise SemanticProcessError('process-monitor-unavailable') from error
                if cancel.is_set():
                    reason = 'cancelled'
                elif overflow.is_set():
                    reason = 'output-limit'
                elif rss > memory_bytes:
                    reason = 'memory-limit'
                elif time.monotonic() >= deadline:
                    reason = 'timeout'
                if reason:
                    break
                cancel.wait(0.02)
        except psutil.NoSuchProcess:
            pass
        finally:
            _stop_process(process, list(children.values()))
            for reader in readers:
                reader.join(timeout=2)
        if any(reader.is_alive() for reader in readers):
            raise SemanticProcessError('pipe-cleanup-failed')
        if reason or overflow.is_set():
            raise SemanticProcessError(reason or 'output-limit')
        return ProcessResult(process.returncode, bytes(buffers[0]), bytes(buffers[1]))
