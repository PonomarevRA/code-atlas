"""Bounded presence-only engine diagnostics for local users and authorized agents."""
from itertools import islice
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from urllib.parse import quote

from .semantic_engine_settings import FIELDS
from .semantic_process import discover_executable, discover_node_executable
from .semantic_provider_registry import create_semantic_provider
from .store import StoreError

VERSION = 'atlas-semantic-engine-diagnostics-v1'
MAX_CANDIDATES = 3


def settings_contract():
    """Describe reversible host-global settings without treating discovery as authorization."""
    return {'schemaVersion': VERSION, 'scope': 'host-global', 'agentRequired': False,
            'guideUrl': '/engine-setup.html', 'markdownGuideUrl': '/agent-setup.md#local-semantic-engines',
            'settingsUrl': '/api/semantic-engines/settings',
            'diagnosticsUrlTemplate': '/api/mods/{modId}/semantic-engine-diagnostics',
            'saveUrlTemplate': '/api/mods/{modId}/semantic-engine-settings',
            'saveMethod': 'POST', 'saveBody': {'paths': {'fieldName': '/absolute/path'}, 'mode': 'patch'},
            'resetFieldBody': {'paths': {'fieldName': ''}, 'mode': 'patch'},
            'resetModBody': {'paths': {}, 'mode': 'replace'},
            'compatibilityCheck': {'method': 'POST', 'urlTemplate': '/api/projects/{projectId}/semantic-enrichment',
                                   'body': {'snapshotId': 'from /api/projects', 'force': True, 'nodeIds': []}},
            'authorization': 'Inspecting a page or this contract does not authorize changing settings. Follow the user request.',
            'presenceIsCompatibility': False, 'autoInstall': False}


def semantic_mod(store, mod_id):
    """Select one installed declarative provider; error records never select executable strategies."""
    item = next((item for item in store.mods.catalog()['mods']
                 if item['id'] == mod_id and item.get('manifest')), None)
    providers = (item or {}).get('manifest', {}).get('analysis', {}).get('semanticProviders', [])
    if len(providers) != 1 or providers[0]['strategy'] not in FIELDS:
        raise StoreError('Семантический мод не найден или его профиль неоднозначен.', 404)
    return {**providers[0], 'modId': mod_id}


def _existing(values):
    """Keep at most three absolute regular-file spellings; preserve venv interpreter identity."""
    selected = []
    for value in values:
        if value is None:
            continue
        path = Path(value).expanduser()
        try:
            if path.is_absolute() and path.is_file():
                spelling = str(path.absolute())
                if len(spelling) <= 4096 and not any(ord(char) < 32 for char in spelling) and spelling not in selected:
                    selected.append(spelling)
        except OSError:
            continue
        if len(selected) == MAX_CANDIDATES:
            break
    return selected


def _typescript(root):
    """Inspect the same fixed project/global TS locations as the source-only provider."""
    node = discover_node_executable()
    values = [os.environ.get('ATLAS_TYPESCRIPT_PATH')]
    if root is not None:
        values.append(root / 'node_modules/typescript/lib/typescript.js')
    if node is not None:
        values.append(node.parent.parent / 'lib/node_modules/typescript/lib/typescript.js')
    return {'nodePath': _existing([node]), 'enginePath': _existing(values)}


def _python(root):
    """List existing known interpreters without importing Jedi or any project module."""
    values = [os.environ.get('ATLAS_JEDI_PYTHON')]
    if root is not None:
        values.extend(root / relative for relative in ('.venv/bin/python', 'venv/bin/python',
                                                       '.venv/Scripts/python.exe', 'venv/Scripts/python.exe'))
    if not getattr(sys, 'frozen', False):
        values.append(sys.executable)
    values.extend((discover_executable('python3'), discover_executable('python')))
    return {'pythonPath': _existing(values)}


def _roslyn():
    """Inspect fixed runtime/bridge paths and at most64 SDK entries; never invoke dotnet/MSBuild."""
    from .semantic_providers.roslyn import RoslynProvider
    from .semantic_providers.roslyn_context import _version
    dotnet = discover_executable('dotnet', project_candidates=(
        Path(os.environ.get('DOTNET_ROOT', '/nonexistent')) / ('dotnet.exe' if os.name == 'nt' else 'dotnet'),
        Path('/usr/local/share/dotnet/dotnet'), Path('/usr/share/dotnet/dotnet')))
    sdks = []
    if dotnet is not None:
        directory = dotnet.resolve().parent / 'sdk'
        if directory.is_dir():
            installed = [path for path in islice(directory.iterdir(), 64) if _version(path)]
            for path in sorted(installed, key=_version, reverse=True):
                if all((path / 'Roslyn/bincore' / name).is_file()
                       for name in ('Microsoft.CodeAnalysis.dll', 'Microsoft.CodeAnalysis.CSharp.dll')):
                    sdks.append(str(path.absolute()))
                    if len(sdks) == MAX_CANDIDATES:
                        break
    return {'dotnetPath': _existing([dotnet]), 'workerPath': _existing([RoslynProvider('diagnostics')._worker()]),
            'sdkPath': sdks}


def _candidates(declaration, root):
    """Dispatch only fixed host strategies; provider candidate methods perform no process work."""
    strategy = declaration['strategy']
    if strategy == 'typescript-local-v1':
        return _typescript(root)
    if strategy == 'jedi-local-v1':
        return _python(root)
    if strategy == 'roslyn-local-v1':
        return _roslyn()
    provider = create_semantic_provider(declaration)
    if strategy == 'phpactor-local-v1':
        php, engines = provider.installations()
        return {'phpPath': _existing([php]), 'enginePath': _existing(engines)}
    if strategy == 'clangd-local-v1':
        return {'enginePath': _existing(provider._candidates())}
    return {'enginePath': _existing(provider.candidates())}


def engine_diagnostics(store, settings, mod_id, project_id=None):
    """Return current overrides and presence candidates without writes, probes or recursive scans."""
    declaration = semantic_mod(store, mod_id)
    root = None
    if project_id is not None:
        with store.lock:
            root = Path(store._project(project_id)['path'])
    try:
        paths = settings.read().get(mod_id, {})
        candidates = _candidates(declaration, root)
    except (OSError, ValueError, AttributeError) as error:
        raise StoreError('Диагностика путей недоступна. Проверьте настройки и установленные файлы.', 409) from error
    fields = FIELDS[declaration['strategy']]
    return {**settings_contract(), 'modId': mod_id, 'projectId': project_id,
            'strategy': declaration['strategy'], 'language': declaration['language'],
            'paths': paths, 'mode': 'explicit' if paths else 'automatic',
            'checkedAt': datetime.now(timezone.utc).isoformat(),
            'fieldModes': {name: 'explicit' if name in paths else 'automatic' for name in fields},
            'fields': list(fields), 'candidates': {name: candidates.get(name, []) for name in fields},
            'candidateStatus': 'presence-only', 'compatibility': 'unchecked',
            'diagnosticsUrl': '/api/mods/' + quote(mod_id, safe='') + '/semantic-engine-diagnostics',
            'limits': {'candidatesPerField': MAX_CANDIDATES, 'sdkEntries': 64, 'recursiveSearch': False},
            'warning': 'Найдены только существующие файлы. Совместимость, версии и контекст карты ещё не проверены. Явные пути не изменены.'}
