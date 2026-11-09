"""Explicit mod catalog outside repositories, pinned by manifest and asset content hashes."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import threading
import re
from itertools import islice
from .mod_models import ModManifest, ID
from .runtime_paths import resource_root
from .source_viewer import _read_regular_file, SourceError

MAX_MODS = 64
MAX_ASSETS = 64
MAX_BYTES = 4 * 1024 * 1024


class ModError(ValueError):
    """Malformed or changed mods are unavailable; the host keeps its built-in features."""


def relative_asset(value: str) -> Path:
    """Assets cannot traverse to another mod, project or user configuration."""
    if not value or len(value) > 200 or '\\' in value or ':' in value or value.startswith('/') or any(part in {'', '.', '..'} for part in value.split('/')):
        raise ModError('Invalid mod asset path')
    return Path(value)


class ModStore:
    """Discovery never imports user code. Activation pins exact contents, API and dependencies."""
    def __init__(self, data_dir: Path, bundled_dir: Path | None = None, *, usage=None):
        self.usage = usage
        self.directory = Path(data_dir) / 'mods'
        # PyInstaller macOS links its host-owned Frameworks/mods to Resources/mods.
        # Resolve only that bundled base; user packages/assets still reject symlinks.
        self.bundled = bundled_dir if bundled_dir is not None else (resource_root() / 'mods').resolve()
        self.state_path = Path(data_dir) / 'mod-state.json'
        self.lock = threading.RLock()
        self.cache = {}

    def state(self) -> dict:
        """An absent state means no optional mods enabled; corrupt state cannot activate anything."""
        try:
            if self.state_path.stat().st_size > 32768:
                return {'__autoAnalysisDisabled__': True}
            value = json.loads(self.state_path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                return {'__autoAnalysisDisabled__': True}
            for key, selected in value.items():
                valid = (type(selected) is bool if key == '__autoAnalysisDisabled__' else
                         selected is False or isinstance(selected, str) and re.fullmatch(r'[a-f0-9]{64}', selected))
                if not valid:
                    return {'__autoAnalysisDisabled__': True}
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {'__autoAnalysisDisabled__': True}

    def _read(self, root: Path) -> dict:
        """Read bounded non-symlink files; a hash change requires explicit reactivation."""
        try:
            paths = sorted(islice(root.rglob('*'),129))
            if len(paths)>128:
                raise ModError('Mod contains too many paths')
            stamps=[]
            for path in [root,*paths]:
                if path.is_symlink(): raise ModError('Mod symlinks are not supported')
                status=path.stat()
                stamps.append((str(path),status.st_mtime_ns,status.st_ctime_ns,status.st_size,status.st_ino))
            previous=self.cache.get(str(root))
            if previous and previous[0]==stamps:
                return deepcopy(previous[1])
            content = _read_regular_file(root, Path('mod.json'), 32768)
            manifest = ModManifest.model_validate_json(content)
            if root.name != manifest.id:
                raise ModError('Folder name must match manifest id')
            digest = sha256(content)
            total, assets = len(content), {}
            for path in paths:
                if path.is_dir() and not path.is_symlink():
                    continue
                relative = path.relative_to(root)
                if relative.name == 'mod.json':
                    continue
                if len(assets) >= MAX_ASSETS:
                    raise ModError('Mod contains more than 64 assets')
                payload = _read_regular_file(root, relative, 1024 * 1024)
                total += len(payload)
                if total > MAX_BYTES:
                    raise ModError('Mod exceeds 4 MiB')
                digest.update(relative.as_posix().encode()); digest.update(b'\0'); digest.update(payload)
                assets[relative.as_posix()] = {'size':len(payload),'hash':sha256(payload).hexdigest()}
            required = [manifest.frontend, *[item.worker for item in manifest.grouping if item.mode == 'python']]
            for value in required:
                if value is None:
                    if any(item.mode == 'python' and item.worker is None for item in manifest.grouping):
                        raise ModError('Python grouping requires a worker')
                    continue
                relative_asset(value)
                if value not in assets:
                    raise ModError('Declared mod entry is missing')
            if manifest.frontend and not manifest.frontend.endswith('.js'):
                raise ModError('Frontend entry must be an ES module .js file')
            if any(item.mode == 'python' and not item.worker.endswith('.py') for item in manifest.grouping):
                raise ModError('Python workers must be .py files')
            if len({g.id for g in manifest.grouping}) != len(manifest.grouping):
                raise ModError('Duplicate grouping id')
            if len({workflow.id for workflow in manifest.workflows}) != len(manifest.workflows):
                raise ModError('Duplicate workflow id')
            if manifest.id in manifest.requires or len(set(manifest.requires)) != len(manifest.requires) or any(not re.fullmatch(ID,x) for x in manifest.requires):
                raise ModError('Invalid mod dependencies')
            record={'manifest':manifest.model_dump(exclude_none=True), 'fingerprint':digest.hexdigest(),
                    'root':root, 'assets':assets, 'bytes':total}
            if len(self.cache)>=64: self.cache.pop(next(iter(self.cache)))
            self.cache[str(root)]=(stamps,deepcopy(record))
            return record
        except (SourceError, OSError, ValueError) as error:
            raise ModError(str(error)[:1000]) from error

    def catalog(self, *, internal=False) -> dict:
        """Bad mods are listed independently; they never prevent maps or other mods from loading."""
        with self.lock:
            enabled = self.state()
            items, identifiers = [], set()
            truncated = False
            for base, origin in [(self.bundled,'bundled'), (self.directory,'user')]:
                if not base.is_dir() or base.is_symlink():
                    continue
                for root in sorted(islice(base.iterdir(), MAX_MODS + 1)):
                    if len(items) >= MAX_MODS:
                        truncated = True; break
                    if not root.is_dir() or root.is_symlink():
                        continue
                    try:
                        item = self._read(root)
                        identifier = item['manifest']['id']
                        if identifier in identifiers:
                            raise ModError('Duplicate mod id; bundled mod cannot be replaced')
                        identifiers.add(identifier)
                        selected = enabled.get(identifier) == item['fingerprint']
                        status = 'enabled' if selected else 'changed' if isinstance(enabled.get(identifier), str) else 'disabled'
                        item.update(id=identifier, enabled=selected, status=status, origin=origin)
                        from .mod_auto_activation import auto_eligible
                        item['autoAnalysisEligible'] = auto_eligible(item)
                        item['analysisMode'] = ('on' if selected else 'off' if enabled.get(identifier) is False
                            else 'changed' if status == 'changed' else 'auto' if auto_eligible(item) else 'manual')
                        if item['analysisMode'] == 'auto':
                            item['status'] = 'auto'
                    except ModError as error:
                        item = {'id':root.name, 'enabled':False, 'status':'error', 'error':str(error), 'origin':origin}
                    items.append(item)
            available = {item['id']:item for item in items if 'manifest' in item}
            visiting, visited, cycles = [], set(), set()

            def visit(identifier):
                """Reject dependency cycles before code activation, including corrupt persisted state."""
                if identifier in visiting:
                    cycles.update(visiting[visiting.index(identifier):]);return
                if identifier in visited or identifier not in available: return
                visiting.append(identifier)
                for dependency in available[identifier]['manifest']['requires']: visit(dependency)
                visiting.pop();visited.add(identifier)

            for identifier in available: visit(identifier)
            for identifier in cycles:
                available[identifier].update(enabled=False,status='error',error='Cyclic mod dependencies')
            active = {item['id'] for item in items if item.get('enabled')}
            while True:
                invalid = [item for item in items if 'manifest' in item and item['id'] in active and not set(item['manifest']['requires']) <= active]
                if not invalid:
                    break
                for item in invalid:
                    active.remove(item['id']); item.update(enabled=False, status='missing-dependency')
            if not internal:
                items = [{k:deepcopy(v) for k,v in item.items() if k not in {'root','assets'}} for item in items]
            return {'apiVersion':1, 'directory':str(self.directory), 'mods':items, 'truncated':truncated,
                    'autoAnalysisEnabled': enabled.get('__autoAnalysisDisabled__') is not True,
                    'notice':'Executable mods run with the application user permissions; they are not sandboxed.'}

    def enable(self, identifier: str, fingerprint: str | None, enabled: bool | None) -> dict:
        """Only the displayed exact version is enabled; missing dependencies are not silently activated."""
        with self.lock:
            catalog = self.catalog(internal=True)
            item = next((m for m in catalog['mods'] if m['id']==identifier and 'manifest' in m), None)
            if not item:
                raise ModError('Mod not found or incompatible')
            if enabled is None and (not item.get('autoAnalysisEligible') or fingerprint != item['fingerprint']):
                raise ModError('Automatic analysis requires the displayed bundled analysis version')
            if enabled and fingerprint != item['fingerprint']:
                raise ModError('Mod changed; reload catalog and explicitly enable this version')
            if enabled:
                if item['status']=='error': raise ModError(item.get('error','Mod is incompatible'))
                active = {m['id'] for m in catalog['mods'] if m.get('enabled')}
                if not set(item['manifest']['requires']) <= active:
                    raise ModError('Enable the declared dependencies first')
            state = self.state()
            if enabled is True: state[identifier] = item['fingerprint']
            elif enabled is False: state[identifier] = False
            else: state.pop(identifier, None)
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state), encoding='utf-8'); temporary.replace(self.state_path)
            return self.catalog()

    def disable_all(self) -> dict:
        """Reset only activation state; installed packages and project maps remain intact."""
        with self.lock:
            if self.state_path.exists():
                temporary=self.state_path.with_suffix('.tmp')
                state = {key: False for key in self.state() if not key.startswith('__')}
                state['__autoAnalysisDisabled__'] = True
                temporary.write_text(json.dumps(state),encoding='utf-8');temporary.replace(self.state_path)
            else:
                self.set_analysis_auto(False)
            return self.catalog()

    def set_analysis_auto(self, enabled: bool) -> dict:
        """Меняет только глобальный auto; ручные on/off остаются приоритетными."""
        with self.lock:
            state = self.state()
            state['__autoAnalysisDisabled__'] = not enabled
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state), encoding='utf-8')
            temporary.replace(self.state_path)
            return self.catalog()

    def active(self, identifier: str, fingerprint: str) -> dict:
        """A disabled, updated or conflicting package cannot serve executable assets."""
        for item in self.catalog(internal=True)['mods']:
            if item['id']==identifier and item.get('enabled') and item['fingerprint']==fingerprint:
                return item
        raise ModError('Mod version is not active')
