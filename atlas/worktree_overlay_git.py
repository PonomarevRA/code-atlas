"""Capture bounded raw file identities in an exact local Git worktree."""

from hashlib import new, sha256
from pathlib import Path, PurePosixPath
import re

from .checkout_maps.errors import CheckoutMapError
from .git_compare import GitCompareError, _git
from .source_viewer import SourceError, _read_regular_file


MAX_CHANGED_FILES = 24
_MAX_FILES = 12_000
_MAX_BYTES = 256 * 1024 * 1024
_MAX_FILE_BYTES = 32 * 1024 * 1024
_CONFIG_SUFFIXES = {'.csproj', '.fsproj', '.vbproj', '.sln', '.slnx', '.props', '.targets'}
_CONFIG_NAMES = {'.editorconfig', '.gitignore', '.gitattributes', '.gitmodules', '.graphifyignore',
                 'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lock',
                 'global.json', 'nuget.config', 'project.assets.json', 'pyproject.toml',
                 'requirements.txt', 'uv.lock', 'go.mod', 'cargo.toml', 'cargo.lock'}


def conflict(message):
    """Return an actionable failure when the pilot cannot certify its scope."""
    return CheckoutMapError(message, 409, 'worktree_overlay_unavailable')


def git(root, *arguments):
    """Reuse the bounded offline Git reader, without executing worktree filters."""
    try:
        return _git(root, ['--no-replace-objects', *arguments])
    except GitCompareError as error:
        raise conflict(str(error)) from error


def _path_name(raw):
    """Reject ambiguous or unrepresentable source identities."""
    try:
        name = raw.decode('utf-8')
    except UnicodeError as error:
        raise conflict('The worktree contains a non-UTF-8 path.') from error
    if (len(name) > 2048 or '\\' in name or ':' in name or any(ord(char) < 32 for char in name)
            or any(part in {'', '.', '..'} or part.endswith((' ', '.')) for part in name.split('/'))):
        raise conflict('The worktree contains a path outside the supported pilot scope.')
    return name


def identity(root):
    """Require an exact Git root and identify its repository and checked-out commit."""
    try:
        top = git(root, 'rev-parse', '--show-toplevel').rstrip(b'\n').decode()
        if Path(top).resolve() != root:
            raise conflict('Select the exact Git worktree root for the overlay.')
        value = git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir', 'HEAD')
        common, _, head = value.rstrip(b'\n').rpartition(b'\n')
        if not re.fullmatch(rb'[0-9a-f]{40}|[0-9a-f]{64}', head):
            raise conflict('The worktree must have an exact checked-out commit.')
        return {'commonDir': str(Path(common.decode()).resolve()), 'commit': head.decode()}
    except (OSError, UnicodeError) as error:
        raise conflict('The Git worktree identity could not be verified.') from error


def _index(root):
    """Validate regular merged entries and prevent sparse or hidden index state."""
    result = {}
    for entry in git(root, 'ls-files', '-v', '--stage', '-z').split(b'\0')[:-1]:
        header, raw_name = entry.split(b'\t', 1)
        tag, mode, oid, stage = header.split(b' ')
        if tag != b'H' or mode not in {b'100644', b'100755'} or stage != b'0':
            raise conflict('Sparse, assumed-unchanged, nonregular or unmerged index entries require a separate build.')
        result[_path_name(raw_name)] = (mode, oid)
    return result


def _inventory(root):
    """Discover paths through Git metadata without diff/status or content conversion."""
    if git(root, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z'):
        raise conflict('The pilot requires a worktree without ignored files; use a clean Git worktree.')
    index = _index(root)
    untracked = git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')[:-1]
    names = set(index) | {_path_name(name) for name in untracked}
    if len(names) > _MAX_FILES:
        raise conflict('The Git inventory exceeds the pilot file budget.')
    return index, names


def _head_tree(root):
    """Read regular paths and modes from HEAD to verify the clean base index."""
    result = {}
    for entry in git(root, 'ls-tree', '-r', '-z', 'HEAD').split(b'\0')[:-1]:
        header, raw_name = entry.split(b'\t', 1)
        mode, kind, oid = header.split(b' ')
        if kind != b'blob' or mode not in {b'100644', b'100755'}:
            raise conflict('A base with symlinks or submodules requires a separate build.')
        result[_path_name(raw_name)] = (mode, oid)
    return result


def _files(root, base_hashes=None):
    """Hash raw regular files and retain bytes only for additions and edits under the delta budget."""
    index, names = _inventory(root)
    hashes, delta = {}, {}
    remaining, delta_bytes = _MAX_BYTES, 4 * 1024 * 1024
    for name in sorted(names):
        try:
            content = _read_regular_file(root, Path(name), min(_MAX_FILE_BYTES, remaining))
        except SourceError as error:
            if base_hashes is not None and error.status == 404:
                candidate = root / name
                if not candidate.exists() and not candidate.is_symlink():
                    continue
            raise conflict('Could not capture regular worktree bytes under the pilot budget.') from error
        remaining -= len(content)
        digest = sha256(content).hexdigest()
        hashes[name] = digest
        if base_hashes is None or base_hashes.get(name) == digest:
            continue
        status = 'modified' if name in base_hashes else 'added'
        if len(content) > min(delta_bytes, 1024 * 1024):
            delta[name] = ({'file': name, 'status': status, 'sourceHash': digest,
                            'issue': 'Declaration byte budget exceeded.'}, None)
        else:
            delta_bytes -= len(content)
            delta[name] = ({'file': name, 'status': status, 'sourceHash': digest, 'issue': None}, content)
    if base_hashes is not None:
        for name in base_hashes.keys() - hashes.keys():
            delta[name] = ({'file': name, 'status': 'deleted', 'sourceHash': None, 'issue': None}, None)
        if len(delta) > MAX_CHANGED_FILES:
            raise conflict(f'The pilot supports at most {MAX_CHANGED_FILES} changed files; rebuild this checkout.')
    return index, hashes, delta


def clean_stamp(root):
    """Hash observed base bytes and require a clean index before and after graph construction."""
    git_identity = identity(root)
    index, hashes, _ = _files(root)
    if index != _head_tree(root) or hashes.keys() != index.keys():
        raise conflict('Build the shared base from a clean worktree.')
    # Reproduce blob identity directly to reject content conversions without running Git filters.
    object_format = 'sha1' if len(git_identity['commit']) == 40 else 'sha256'
    for name, (_, oid) in index.items():
        try:
            content = _read_regular_file(root, Path(name), _MAX_FILE_BYTES)
        except SourceError as error:
            raise conflict('The clean worktree changed while capturing the shared base.') from error
        blob = new(object_format, f'blob {len(content)}\0'.encode())
        blob.update(content)
        if blob.hexdigest().encode() != oid or sha256(content).hexdigest() != hashes[name]:
            raise conflict('The base worktree must match committed bytes exactly; use a fresh worktree without filters.')
    if identity(root) != git_identity or _inventory(root) != (index, set(index)):
        raise conflict('The clean worktree changed while capturing the shared base.')
    return {**git_identity, 'fileHashes': hashes}


def capture_delta(root, git_identity, base_hashes):
    """Return raw changes to a fixed base, including staged and unstaged deletions."""
    if identity(root) != git_identity:
        raise conflict('The worktree commit changed; open a new overlay against a matching base.')
    _, _, delta = _files(root, base_hashes)
    if identity(root) != git_identity:
        raise conflict('The worktree changed during the overlay refresh. Retry.')
    return delta


def configuration_path(name):
    """Recognize edits that may widen rebinding beyond affected source files."""
    path = PurePosixPath(name)
    folded = path.name.casefold()
    return (folded in _CONFIG_NAMES or path.suffix.casefold() in _CONFIG_SUFFIXES
            or folded.startswith(('tsconfig', 'jsconfig')) and folded.endswith('.json')
            or folded.endswith(('.lock', '.lock.json')))
