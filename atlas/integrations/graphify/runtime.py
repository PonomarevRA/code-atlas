"""Select an embedded or independent Graphify interpreter without importing project code."""
from contextlib import contextmanager
from contextvars import ContextVar
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import subprocess
import threading

from ...runtime_paths import resource_root, worker_executable, worker_creation_flags

ADAPTER_VERSION = 'graphify-adapter-v1'
_ACTIVE = ContextVar('atlas_graphify_runtime', default=None)


def probe(python: str | None) -> dict:
    """Inspect the selected installation, never run pip or silently replace a broken runtime."""
    if python is None:
        try:
            vendor = version('graphifyy')
        except PackageNotFoundError:
            vendor = None
        return {'mode':'embedded', 'version':vendor, 'available':vendor is not None, 'adapterVersion':ADAPTER_VERSION}
    path = Path(python).expanduser().absolute()
    if not Path(python).expanduser().is_absolute() or not path.is_file():
        raise ValueError('Choose an absolute path to the independent Python executable')
    script = "import json; from importlib.metadata import version; print(json.dumps({'version':version('graphifyy')}))"
    try:
        result = subprocess.run([str(path), '-I', '-c', script], capture_output=True, timeout=8,
                                creationflags=worker_creation_flags(), check=False)
        if result.returncode or len(result.stdout) > 8192:
            raise ValueError('The selected runtime must contain graphifyy[mcp]')
        vendor = json.loads(result.stdout)['version']
        if not isinstance(vendor, str) or len(vendor) > 128:
            raise ValueError('Invalid Graphify version')
    except (OSError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as error:
        raise ValueError('Unable to inspect the independent Graphify runtime') from error
    return {'mode':'external', 'python':str(path), 'version':vendor, 'available':True, 'adapterVersion':ADAPTER_VERSION}


class GraphifyRuntime:
    """Persistent provider selection is application-owned, outside analyzed repositories."""
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / 'providers' / 'graphify.json'
        self.lock = threading.RLock()

    def selected(self) -> str | None:
        """Environment selection is explicit and wins over the user data configuration."""
        override = os.environ.get('ATLAS_GRAPHIFY_PYTHON')
        if override:
            return override
        with self.lock:
            if not self.path.exists():
                return None
            if self.path.stat().st_size > 8192:
                raise ValueError('Graphify runtime configuration is too large')
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or (value.get('python') is not None and not isinstance(value['python'], str)):
                raise ValueError('Invalid Graphify runtime configuration')
            return value.get('python')

    def info(self) -> dict:
        """Report absence/failure without preventing Atlas from opening existing maps."""
        try:
            return probe(self.selected())
        except (ValueError, OSError) as error:
            return {'mode':'external', 'available':False, 'version':None, 'error':str(error)[:500], 'adapterVersion':ADAPTER_VERSION}

    def set(self, python: str | None) -> dict:
        """Validate before committing a new runtime; resetting does not delete maps or modules."""
        if os.environ.get('ATLAS_GRAPHIFY_PYTHON'):
            raise ValueError('ATLAS_GRAPHIFY_PYTHON controls the runtime; change that environment setting first')
        info = probe(python)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'python':info.get('python')}), encoding='utf-8')
            temporary.replace(self.path)
        return info

    @contextmanager
    def activate(self):
        """Capture selection for this build/MCP task; concurrent updates cannot switch it mid-command."""
        token = _ACTIVE.set(self.selected())
        try:
            yield
        finally:
            _ACTIVE.reset(token)


def command(module: str, arguments: list[str]) -> list[str]:
    """External interpreter runs a shipped standalone offline runner, not the Atlas environment."""
    python = _ACTIVE.get() or os.environ.get('ATLAS_GRAPHIFY_PYTHON')
    if python:
        return [python, '-I', str(resource_root() / 'resources/integrations/graphify/runner.py'), module, *arguments]
    return [worker_executable(), '-m', 'atlas.offline_runner', module, *arguments]


def current_info() -> dict:
    """Attach integration provenance to newly normalized snapshots."""
    return probe(_ACTIVE.get() or os.environ.get('ATLAS_GRAPHIFY_PYTHON'))
