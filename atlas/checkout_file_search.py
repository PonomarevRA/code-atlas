"""Bounded lexical declarations from one working-tree file, independent of a graph."""

from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path

from tree_sitter import Language, Parser

from .code_health_source import language_for
from .member_declarations import callable_initializer, declaration_name, method_parameters
from .project_file_inventory import SKIP_DIRECTORIES
from .source_viewer import MAX_SOURCE_BYTES, SourceError, _read_regular_file, _source_path
from .store import StoreError


MAX_AST_NODES = 20_000
MAX_DECLARATION_CHARS = 240
VERSION = 'checkout-file-declarations-v1'


def _file_path(root: Path, name: str) -> Path:
    """Require an exact slash-separated checkout path without aliases or cache directories."""
    parts = name.split('/')
    if (any(part in {'', '.', '..'} or part.endswith((' ', '.')) for part in parts)
            or any(char in name for char in '\\:')
            or any(ord(char) < 32 for char in name)
            or any(part.casefold() in SKIP_DIRECTORIES for part in parts)):
        raise SourceError('Use an exact repository-relative source path.', 403)
    actual_root, relative = _source_path(root, {'file': name})
    if actual_root != root or relative.as_posix() != name:
        raise SourceError('Source path changed or points to a symbolic link.', 403)
    return relative


def _build_info(entry) -> dict:
    """Sample build metadata without reading the graph or extending the session."""
    state = entry.store.list_projects()
    project = next(item for item in state['projects'] if item['id'] == entry.project_id)
    job = state.get('activeJob') or {}
    return {'jobId': job.get('id'), 'status': project['status'],
            'publishedSnapshotId': project.get('snapshotId'),
            'snapshotAvailable': project.get('snapshotAvailable', False)}


def _generation(build: dict) -> str | None:
    """Keep one identity as the active job becomes its published snapshot."""
    return build['jobId'] or build['publishedSnapshotId']


def _declaration(node, source: bytes, language) -> tuple[str, str, str]:
    """Extract the lexical name and parameter text using Atlas grammar helpers."""
    if node.type in language.classes:
        return declaration_name(node, source), 'type', ''
    if node.type in language.methods:
        kind = 'constructor' if node.type == 'constructor_declaration' else 'callable'
        return declaration_name(node, source), kind, method_parameters(node, source)
    if language.name in {'javascript', 'typescript'} and node.type == 'variable_declarator':
        initializer = callable_initializer(node.child_by_field_name('value'))
        if initializer is not None:
            params = method_parameters(initializer, source)
            parameter = initializer.child_by_field_name('parameter')
            if not params and parameter is not None:
                params = source[parameter.start_byte:parameter.end_byte].decode('utf-8')
            return declaration_name(node, source), 'callable', params
    return '', '', ''


def _search_declarations(tree, source: bytes, language, query: str, limit: int) -> dict:
    """Find source-order matches under a fixed visit budget, skipping recovery declarations."""
    pending = [(tree.root_node, '')]
    visited = total = 0
    matches = []
    term = query.casefold()
    while pending and visited < MAX_AST_NODES:
        node, owner = pending.pop()
        visited += 1
        if node.is_error or node.is_missing:
            continue
        if node.has_error and node.type in language.classes | language.methods:
            # A recovered child would have an uncertain owner or declaration range.
            continue
        name, kind, params = ('', '', '') if node.has_error else _declaration(node, source, language)
        if name:
            qualified = f'{owner}.{name}' if owner else name
            if term in qualified.casefold():
                total += 1
                if len(matches) < limit:
                    matches.append({
                        'name': name[:MAX_DECLARATION_CHARS], 'kind': kind,
                        'owner': owner[:MAX_DECLARATION_CHARS], 'line': node.start_point.row + 1,
                        'endLine': node.end_point.row + 1,
                        'parameterList': params[:MAX_DECLARATION_CHARS],
                        'textTruncated': max(len(name), len(owner), len(params)) > MAX_DECLARATION_CHARS,
                    })
            owner = qualified
        pending.extend((child, owner) for child in reversed(node.named_children))
    syntax_errors = tree.root_node.has_error
    return {'declarations': matches, 'total': total,
            'truncated': bool(pending) or total > limit,
            'coverage': {'status': 'partial' if syntax_errors or pending else 'complete',
                         'syntaxErrors': syntax_errors, 'astLimited': bool(pending),
                         'visitedAstNodes': visited, 'maxAstNodes': MAX_AST_NODES,
                         'maxFileBytes': MAX_SOURCE_BYTES, 'resultLimit': limit,
                         'declarationKinds': ['type', 'callable', 'constructor']}}


def lexical_declarations(content, language, query, limit):
    """Parse bounded UTF-8 bytes with the selected grammar and report recovery limits."""
    try:
        if b'\0' in content:
            raise UnicodeError
        source = content.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
    except UnicodeError as error:
        raise StoreError('Source must be UTF-8 text.', 415) from error
    try:
        module = import_module(language.module)
        parser = Parser(Language(getattr(module, language.language_function)()))
    except (ImportError, AttributeError, ValueError) as error:
        raise StoreError('Declaration grammar is unavailable.', 503) from error
    return _search_declarations(parser.parse(source), source, language, query, limit)


def search_checkout_file(entry, path: str, query: str, limit: int) -> dict:
    """Read a leased session's source and return file-local evidence without a snapshot lookup."""
    root = Path(entry.record['checkout'])
    build_before = _build_info(entry)
    try:
        relative = _file_path(root, path)
        language = language_for(path)
        if language is None:
            raise SourceError('No declaration grammar is available for this source type.', 415)
        content = _read_regular_file(root, relative)
        read_at = datetime.now(timezone.utc).isoformat()
    except SourceError as error:
        raise StoreError(str(error), error.status) from error
    result = lexical_declarations(content, language, query, limit)
    try:
        unchanged = _read_regular_file(root, relative) == content
    except SourceError:
        unchanged = False
    if not unchanged:
        raise StoreError('Source changed during file search. Retry with current source.', 409)

    build = _build_info(entry)
    if _generation(build) != _generation(build_before):
        raise StoreError('Checkout map build changed during file search. Retry.', 409)
    digest = sha256(content).hexdigest()
    revision = json.dumps([VERSION, entry.session_id, _generation(build), path, digest],
                          separators=(',', ':')).encode()
    return {'scope': 'file', 'sourceKind': 'working-tree', 'projectId': entry.project_id,
            'file': path, 'language': language.name, 'sourceHash': digest, 'sourceReadAt': read_at,
            'previewRevision': sha256(revision).hexdigest(), 'build': build, **result}
