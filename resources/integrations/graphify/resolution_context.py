"""
Hash bounded resolver configuration without importing analyzed project code.
"""

from hashlib import sha256
import json
import os
from pathlib import Path


CONFIG_NAMES = {'.gitignore', '.graphifyignore', 'package.json', 'package-lock.json',
                'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'yarn.lock', 'bun.lock',
                'pyproject.toml', 'setup.py', 'setup.cfg', 'go.mod', 'go.work',
                'Cargo.toml', 'Cargo.lock', 'global.json', 'nuget.config'}
CONFIG_SUFFIXES = {'.csproj', '.fsproj', '.vbproj', '.sln', '.slnx', '.props', '.targets'}
SKIP_DIRECTORIES = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', 'vendor',
                    'obj', 'bin', 'dist', 'build', 'outputs', '.atlas'}
MAX_CONFIG_FILES = 4096
MAX_CONFIG_BYTES = 64 * 1024 * 1024
MAX_DIRECTORIES = 20000


def _candidate(path):
    """
    Select resolver/build settings and ignore rules, not arbitrary JSON datasets.
    """
    name = path.name
    return (name in CONFIG_NAMES or path.suffix.lower() in CONFIG_SUFFIXES
            or name.endswith('.json') and name.startswith(('tsconfig', 'jsconfig')))


def configuration_state(root):
    """
    Fingerprint known in-root resolver settings, including local config references.

    The traversal does not follow links or walk installed dependency trees.
    JSONC extends/references are followed only within the analyzed source root.
    Exceeding the explicit discovery/read budget fails rather than certifying a
    partial configuration identity.
    """
    root = Path(root).resolve()
    pending, directories = set(), 0
    for folder, children, files in os.walk(root, followlinks=False):
        directories += 1
        if directories > MAX_DIRECTORIES:
            raise RuntimeError('Resolver configuration directory budget exceeded')
        parent = Path(folder)
        kept = []
        for name in children:
            candidate = parent / name
            redirected = candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction())
            if name not in SKIP_DIRECTORIES and not redirected:
                kept.append(name)
        children[:] = kept
        for name in files:
            path = parent / name
            if _candidate(path) and not path.is_symlink():
                pending.add(path)
        if len(pending) > MAX_CONFIG_FILES:
            raise RuntimeError('Resolver configuration file budget exceeded')
    records, visited, total_bytes = [], set(), 0
    while pending:
        path = min(pending)
        pending.remove(path)
        if path in visited:
            continue
        visited.add(path)
        if len(visited) > MAX_CONFIG_FILES:
            raise RuntimeError('Resolver configuration file budget exceeded')
        digest, content = sha256(), bytearray()
        with path.open('rb') as source:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                total_bytes += len(block)
                if total_bytes > MAX_CONFIG_BYTES:
                    raise RuntimeError('Resolver configuration byte budget exceeded')
                digest.update(block)
                if len(content) < 1024 * 1024:
                    content.extend(block)
        records.append({'file': path.relative_to(root).as_posix(), 'sha256': digest.hexdigest()})
        if path.suffix.lower() != '.json' or path.stat().st_size > 1024 * 1024:
            continue
        try:
            from graphify.extractors.resolution import _strip_jsonc
            config = json.loads(_strip_jsonc(content.decode('utf-8-sig')))
        except (UnicodeError, ValueError):
            continue
        if not isinstance(config, dict):
            continue
        references = config.get('references', [])
        extends = config.get('extends', [])
        if isinstance(extends, str):
            extends = [extends]
        targets = list(extends) if isinstance(extends, list) else []
        if isinstance(references, list):
            for reference in references:
                if isinstance(reference, dict):
                    targets.append(reference.get('path'))
        for target in targets:
            if not isinstance(target, str) or not target:
                continue
            candidate = (path.parent / target).absolute()
            # Existing symlink segments are never followed during config reads.
            current = candidate
            redirected = False
            while current != root and root in current.parents:
                redirected |= current.is_symlink()
                current = current.parent
            resolved = candidate.resolve()
            if redirected or not resolved.is_relative_to(root):
                continue
            if resolved.is_dir():
                resolved = resolved / 'tsconfig.json'
            elif not resolved.suffix:
                resolved = resolved.with_suffix('.json')
            if resolved.is_file() and not resolved.is_symlink() and resolved not in visited:
                pending.add(resolved)
    records.sort(key=lambda item: item['file'])
    encoded = json.dumps(records, sort_keys=True, separators=(',', ':')).encode()
    return {'fingerprint': sha256(encoded).hexdigest(), 'files': records,
            'coverage': 'known-in-root-resolver-configs', 'bytes': total_bytes}
