"""Host-local optional engine paths, independent of manifests and structural map identity."""
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock

MAX_BYTES = 64_000
MAX_MODS = 64

FIELDS = {
    'typescript-local-v1': ('enginePath', 'nodePath'),
    'jedi-local-v1': ('pythonPath',),
    'roslyn-local-v1': ('dotnetPath', 'workerPath', 'sdkPath'),
    'phpactor-local-v1': ('enginePath', 'phpPath'),
    'jdt-local-v1': ('enginePath',), 'gopls-local-v1': ('enginePath',),
    'clangd-local-v1': ('enginePath',), 'kotlin-local-v1': ('enginePath',),
    'rust-analyzer-local-v1': ('enginePath',), 'sourcekit-local-v1': ('enginePath',),
}


class SemanticEngineSettings:
    """Store paths only: no shell command, arbitrary arguments, installer or repository code."""
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'semantic-engine-settings.json'
        self.lock = RLock()

    def read(self):
        """Read bounded settings; corrupt content never enables a different executable."""
        try:
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError('Oversized engine settings')
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('version') != 1 or not isinstance(value.get('mods'), dict) or len(value['mods']) > MAX_MODS:
                raise ValueError('Invalid engine settings')
            for paths in value['mods'].values():
                if not isinstance(paths, dict) or len(paths) > 4 or any(
                    not isinstance(path, str) or len(path) > 4096 or not Path(path).is_absolute()
                    or any(ord(char) < 32 for char in path)
                    for path in paths.values()):
                    raise ValueError('Invalid engine path settings')
            return value['mods']
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, AttributeError, RecursionError) as error:
            # Explicit invalid settings must not silently fall back to automatic
            # discovery of another executable. Surface the configuration error.
            raise ValueError('Настройки путей движков повреждены. Сохраните их заново.') from error

    def save(self, mod_id, strategy, paths, *, mode='replace'):
        """Validate paths; patch atomically preserves other fields, while empty values restore automatic mode."""
        from .store import write_json
        if mode not in {'replace', 'patch'} or strategy not in FIELDS or set(paths) - set(FIELDS[strategy]):
            raise ValueError('Неизвестное поле пути движка.')
        selected = {}
        for name, path in paths.items():
            if not isinstance(path, str) or len(path) > 4096 or any(ord(char) < 32 for char in path):
                raise ValueError('Укажите путь к файлу или каталогу, а не команду.')
            if not path.strip():
                continue
            absolute = Path(path.strip()).expanduser()
            if not absolute.is_absolute():
                raise ValueError('Укажите абсолютный путь; пустое поле включает автопоиск.')
            spelling = str(absolute)
            if len(spelling) > 4096:
                raise ValueError('Развёрнутый путь превышает допустимую длину.')
            selected[name] = spelling
        with self.lock:
            all_mods = self.read()
            if mode == 'patch':
                current = dict(all_mods.get(mod_id, {}))
                for name in paths:
                    current.pop(name, None)
                selected = {**current, **selected}
            if selected:
                all_mods[mod_id] = selected
            else:
                all_mods.pop(mod_id, None)
            payload = {'version': 1, 'mods': all_mods}
            # Match the atomic writer's exact UTF8 encoding. Otherwise accepted
            # patches could make the next read fail, including automatic reset.
            encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            if len(all_mods) > MAX_MODS or len(encoded) > MAX_BYTES:
                raise ValueError('Настройки путей превышают допустимый общий размер; прежние значения сохранены.')
            write_json(self.path, payload)
        return selected

    def fingerprint(self, mod_id):
        """Changing one profile's paths changes its discovery key without rebuilding Graphify."""
        selected = self.read().get(mod_id, {})
        return sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
