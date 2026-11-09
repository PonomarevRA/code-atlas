"""Short-lived, read-only LSP sessions for trusted optional language adapters."""
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
from threading import Event, Thread
import time

from .semantic_process import SemanticProcessError, _stop_process

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_SESSION_BYTES = 8 * 1024 * 1024
MAX_SESSION_MESSAGES = 512


class LspSession:
    """Own one bounded stdio process, without watchers, edits, commands or background reuse.

    Engine profiles must select safe startup settings and an isolated captured
    workspace. This transport never opens the analyzed repository itself.
    """

    def __init__(self, command, workspace, cancel, *, settings=None, timeout=30, environment=None):
        """Start a host-owned absolute command; initialize is the caller's first request."""
        if (not command or not Path(command[0]).is_absolute() or not 0 < timeout <= 60):
            raise ValueError('Invalid LSP command or time budget')
        self.cancel, self.settings = cancel, settings or {}
        self.deadline = time.monotonic() + timeout
        self.messages, self.failed = Queue(maxsize=128), Event()
        self.failure_reason = None
        self.stderr = bytearray()
        self.diagnostics = {}
        self.next_id, self.received_bytes, self.received_messages = 0, 0, 0
        self.children = {}
        self.writer = None
        self.closed = False
        env = dict(os.environ if environment is None else environment)
        for key in ('PYTHONPATH', 'PYTHONSTARTUP', 'NODE_OPTIONS', 'JAVA_TOOL_OPTIONS',
                    '_JAVA_OPTIONS', 'LD_PRELOAD', 'DYLD_INSERT_LIBRARIES', 'CLIENT_PORT', 'CLIENT_HOST'):
            env.pop(key, None)
        env.update({'GOTOOLCHAIN': 'local', 'GOPROXY': 'off', 'GOSUMDB': 'off',
                    'GOPACKAGESDRIVER': 'off', 'CGO_ENABLED': '0',
                    'DOTNET_SKIP_FIRST_TIME_EXPERIENCE': '1', 'NO_COLOR': '1'})
        try:
            self.process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name != 'nt',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as error:
            raise SemanticProcessError('lsp-launch-failed') from error
        self.readers = [Thread(target=self._read_messages, daemon=True),
                        Thread(target=self._read_errors, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _fail(self, reason):
        """Publish a stable transport reason, keeping engine output out of public errors."""
        self.failure_reason = reason
        self.failed.set()

    def _read_messages(self):
        """Decode bounded Content-Length frames; never retain an unbounded notification stream."""
        stream = self.process.stdout
        try:
            while not self.closed:
                length, header_bytes = None, 0
                while True:
                    line = stream.readline(8193)
                    if not line:
                        self._fail('lsp-eof')
                        return
                    header_bytes += len(line)
                    if len(line) > 8192 or header_bytes > 8192:
                        raise ValueError('lsp-header-limit')
                    if line in {b'\r\n', b'\n'}:
                        break
                    name, separator, value = line.partition(b':')
                    if not separator:
                        raise ValueError('lsp-invalid-header')
                    if name.lower() == b'content-length':
                        if length is not None:
                            raise ValueError('lsp-duplicate-length')
                        length = int(value.strip())
                if length is None or not 0 < length <= MAX_MESSAGE_BYTES:
                    raise ValueError('lsp-message-limit')
                self.received_bytes += length
                self.received_messages += 1
                if self.received_bytes > MAX_SESSION_BYTES or self.received_messages > MAX_SESSION_MESSAGES:
                    raise ValueError('lsp-session-limit')
                payload = stream.read(length)
                if len(payload) != length:
                    raise ValueError('lsp-truncated-message')
                message = json.loads(payload)
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                    raise ValueError('lsp-invalid-message')
                self.messages.put_nowait(message)
        except Full:
            self._fail('lsp-message-queue-limit')
        except (OSError, ValueError) as error:
            self._fail(str(error) if str(error).startswith('lsp-') else 'lsp-invalid-message')
        finally:
            stream.close()

    def _read_errors(self):
        """Bound stderr jointly over the session; diagnostics are parsed from LSP only."""
        try:
            while chunk := self.process.stderr.read(4096):
                remaining = 64 * 1024 - len(self.stderr)
                self.stderr.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self._fail('lsp-stderr-limit')
                    return
        finally:
            self.process.stderr.close()

    def _check(self):
        """Check cancellation/deadline and sampled parent/child RSS before each protocol wait."""
        import psutil
        if self.cancel.is_set():
            raise SemanticProcessError('cancelled')
        if time.monotonic() >= self.deadline:
            raise SemanticProcessError('lsp-timeout')
        if self.failed.is_set() and self.messages.empty():
            raise SemanticProcessError(self.failure_reason or 'lsp-failed')
        try:
            parent = psutil.Process(self.process.pid)
            rss = parent.memory_info().rss
            for child in parent.children(recursive=True):
                self.children[child.pid] = child
                try:
                    rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            if rss > 1024 * 1024 * 1024:
                raise SemanticProcessError('lsp-memory-limit')
        except psutil.NoSuchProcess:
            if self.messages.empty():
                raise SemanticProcessError('lsp-exited') from None
        except (psutil.AccessDenied, PermissionError) as error:
            raise SemanticProcessError('process-monitor-unavailable') from error

    def _send(self, message):
        """Write only host-generated JSON-RPC; source text is capped by the caller's request budget."""
        self._check()
        payload = json.dumps({'jsonrpc': '2.0', **message}, ensure_ascii=False).encode('utf-8')
        if len(payload) > 4 * 1024 * 1024:
            raise SemanticProcessError('lsp-input-limit')
        completed, errors = Event(), []

        def write_frame():
            """A stalled engine must not prevent cancellation while its input pipe is full."""
            try:
                self.process.stdin.write(f'Content-Length: {len(payload)}\r\n\r\n'.encode('ascii') + payload)
                self.process.stdin.flush()
            except (OSError, ValueError) as error:
                errors.append(error)
            finally:
                completed.set()

        self.writer = Thread(target=write_frame, daemon=True)
        self.writer.start()
        while not completed.wait(.02):
            self._check()
        if errors:
            raise SemanticProcessError('lsp-write-failed') from errors[0]

    def notify(self, method, params):
        """Send synchronization/lifecycle notifications, never an executeCommand request."""
        self._send({'method': method, 'params': params})

    def request(self, method, params, *, timeout=5):
        """Read through notifications and safe server requests until this request's response."""
        self.next_id += 1
        request_id = self.next_id
        self._send({'id': request_id, 'method': method, 'params': params})
        deadline = min(self.deadline, time.monotonic() + timeout)
        while True:
            self._check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SemanticProcessError('lsp-request-timeout')
            try:
                message = self.messages.get(timeout=min(.05, remaining))
            except Empty:
                continue
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    raise SemanticProcessError('lsp-request-rejected')
                return message.get('result')
            self._service_message(message)

    def _service_message(self, message):
        """Answer configuration/progress only; reject edits, commands and arbitrary callbacks."""
        method, params = message.get('method'), message.get('params') or {}
        if 'id' in message and method:
            if method == 'workspace/configuration':
                items = params.get('items', [])
                if not isinstance(items, list) or len(items) > 32:
                    raise SemanticProcessError('lsp-configuration-limit')
                result = []
                for item in items:
                    section = item.get('section', '')
                    selected = self.settings
                    for key in section.split('.') if section else []:
                        selected = selected.get(key) if isinstance(selected, dict) else None
                    result.append(selected)
                self._send({'id': message['id'], 'result': result})
            elif method in {'window/workDoneProgress/create', 'window/showMessageRequest'}:
                self._send({'id': message['id'], 'result': None})
            else:
                self._send({'id': message['id'], 'error': {'code': -32601, 'message': 'Read-only Atlas client'}})
        elif method == 'textDocument/publishDiagnostics':
            uri, diagnostics = params.get('uri'), params.get('diagnostics')
            if isinstance(uri, str) and isinstance(diagnostics, list) and len(self.diagnostics) < 16:
                self.diagnostics[uri] = diagnostics[:64]

    def initialize(self, workspace, options=None):
        """Negotiate UTF-16 definitions before sending any document notifications."""
        uri = Path(workspace).absolute().as_uri()
        result = self.request('initialize', {'processId': os.getpid(), 'rootUri': uri,
            'workspaceFolders': [{'uri': uri, 'name': 'Atlas captured sources'}],
            'clientInfo': {'name': 'Atlas static semantic adapter', 'version': '1'},
            'capabilities': {'general': {'positionEncodings': ['utf-16']},
                             'workspace': {'configuration': True},
                             'textDocument': {'definition': {'linkSupport': True}}},
            'initializationOptions': options or {}}, timeout=10)
        capabilities = result.get('capabilities', {}) if isinstance(result, dict) else {}
        if not capabilities.get('definitionProvider'):
            raise SemanticProcessError('lsp-definition-unsupported')
        if capabilities.get('positionEncoding', 'utf-16') != 'utf-16':
            raise SemanticProcessError('lsp-position-encoding-unsupported')
        self.notify('initialized', {})
        return result

    def close(self):
        """Close owned pipes/processes even after cancellation, malformed data or timeout."""
        if self.closed:
            return
        try:
            if not self.cancel.is_set() and self.process.poll() is None:
                try:
                    self.request('shutdown', None, timeout=.3)
                    self.notify('exit', {})
                except SemanticProcessError:
                    pass
        finally:
            self.closed = True
            _stop_process(self.process, list(self.children.values()))
            if self.writer is not None:
                self.writer.join(timeout=1)
            self.process.stdin.close()
            for reader in self.readers:
                reader.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
