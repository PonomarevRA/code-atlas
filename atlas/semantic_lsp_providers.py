"""Optional source-anchored LSP profiles; analyzed build scripts are never launched."""
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
from urllib.parse import unquote, urlparse

from .semantic_lsp import LspSession
from .semantic_models import (SemanticAvailability, SemanticCoverage, SemanticFact,
                              SemanticResult, SemanticSymbol, SourceSpan)
from .semantic_process import SemanticProcessError, discover_executable, run_bounded
from .semantic_runtime import ProbeOutcome


def _fingerprint(paths, version):
    """Pin the installed executable, host adapter/transport and static context bytes."""
    digest = sha256(version.encode('utf-8'))
    for path in paths:
        stat = path.stat()
        digest.update(str((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino)).encode())
    return digest.hexdigest()


def _request_sources(context, request):
    """Accept at most sixteen hash-pinned UTF-8 files and sixty-four selected queries."""
    files, queries = request.get('files'), request.get('queries')
    if not isinstance(files, list) or not 1 <= len(files) <= 16 or not isinstance(queries, list) or len(queries) > 64:
        raise ValueError('Invalid bounded LSP source request')
    contents, size = {}, 0
    for file in files:
        path, text, source_hash = file['path'], file['text'], file['sourceHash']
        SourceSpan(path, 0, 0, 0, 0)
        if not isinstance(text, str) or path in contents or context.source_hashes.get(path) != source_hash:
            raise ValueError('Unpinned or duplicate LSP source')
        content = text.encode('utf-8')
        if sha256(content).hexdigest() != source_hash and sha256(b'\xef\xbb\xbf' + content).hexdigest() != source_hash:
            raise ValueError('LSP source text does not match captured bytes')
        size += len(content)
        if size > 4 * 1024 * 1024:
            raise ValueError('LSP source request exceeds budget')
        contents[path] = text
    for query in queries:
        if query.get('path') not in contents:
            raise ValueError('LSP query is outside pinned source request')
        for name in ('line', 'column'):
            if type(query.get(name)) is not int or query[name] < 0:
                raise ValueError('Invalid LSP query position')
        source_span = SourceSpan(**query['sourceSpan'])
        if source_span.path != query['path'] or source_span.coordinate_kind != 'utf-16':
            raise ValueError('LSP source coordinates must be UTF-16')
        lines = contents[query['path']].splitlines(keepends=True)
        if query['line'] >= len(lines):
            raise ValueError('LSP query line is outside captured text')
        maximum = len(lines[query['line']].rstrip('\r\n').encode('utf-16-le')) // 2
        if query['column'] > maximum:
            raise ValueError('LSP query column is outside captured text')
    return contents, queries


def _location_symbol(location, workspace, contents):
    """Map only pinned private-workspace locations back to repository-relative symbols."""
    uri = location.get('targetUri', location.get('uri'))
    span = location.get('targetSelectionRange', location.get('range'))
    if not isinstance(uri, str) or not isinstance(span, dict):
        return None
    parsed = urlparse(uri)
    if parsed.scheme != 'file' or parsed.netloc not in {'', 'localhost'}:
        return None
    try:
        path = Path(unquote(parsed.path)).resolve().relative_to(workspace.resolve()).as_posix()
    except (ValueError, OSError):
        return None
    if path not in contents:
        return None
    start, end = span['start'], span['end']
    source_span = SourceSpan(path, start['line'], start['character'], end['line'], end['character'])
    lines = contents[path].splitlines()
    if source_span.end_line >= len(lines):
        raise ValueError('LSP target is outside captured text')
    for line, column in ((source_span.start_line, source_span.start_column),
                         (source_span.end_line, source_span.end_column)):
        if column > len(lines[line].encode('utf-16-le')) // 2:
            raise ValueError('LSP target column is outside captured text')
    identity = sha256(json.dumps([path, span], sort_keys=True).encode()).hexdigest()
    return SemanticSymbol(identity, 'declaration', path, source_span)


def _definition_facts(session, workspace, context, provider_id, contents, queries, *, callable_target=None):
    """Resolve selected positions without guessing a target from a same-named symbol."""
    facts, resolved, reasons = [], 0, []
    for query in queries:
        source = SemanticSymbol(str(query['sourceNodeId']), 'method', query.get('label') or 'method',
                                SourceSpan(**query['sourceSpan']))
        callsite = SourceSpan(**query['callsite']) if query.get('callsite') else None
        kind = query.get('kind', 'reference')
        kind = kind if kind in {'call', 'reference'} else 'reference'
        try:
            response = session.request('textDocument/definition', {
                'textDocument': {'uri': (workspace / query['path']).as_uri()},
                'position': {'line': query['line'], 'character': query['column']}})
            locations = response if isinstance(response, list) else [response] if response else []
            if len(locations) > 16:
                raise ValueError('LSP definition candidate budget exceeded')
            targets = {}
            for location in locations:
                target = _location_symbol(location, workspace, contents)
                if target:
                    if callable_target is not None and callable_target(target):
                        target = replace(target, kind='function')
                    elif callable_target is not None:
                        # A variable inside a method is a symbol, not that method's
                        # declaration. Do not let containing-range graph binding
                        # turn a function-pointer parameter into a self-call edge.
                        span = target.source_span
                        target = replace(target, kind='local-symbol', source_span=None,
                            uri=f'source-symbol:{span.path}:{span.start_line}:{span.start_column}')
                    targets[target.symbol_id] = target
            if len(targets) == 1:
                target = next(iter(targets.values()))
                confidence = 'confirmed-static'
                evidence = 'LSP definition binding in isolated captured compile context; runtime selection unknown.'
                if kind == 'call' and (callable_target is None or target.kind == 'local-symbol'):
                    confidence = 'candidate'
                    reasons.append('invocation-target-not-a-confirmed-method')
                    evidence = 'LSP resolved a declaration; invocation target is not confirmed as a method.'
                else:
                    resolved += 1
                facts.append(SemanticFact(kind, source, target, confidence, callsite, evidence))
            elif targets:
                reasons.append('ambiguous-definition')
                for target in targets.values():
                    facts.append(SemanticFact(kind, source, target, 'candidate', callsite,
                                              'LSP returned several static definition candidates.'))
            else:
                reasons.append('definition-unavailable-or-outside-pinned-scope')
                facts.append(SemanticFact(kind, source, None, 'unknown', callsite,
                                          'No unique pinned-source definition was returned.'))
        except (SemanticProcessError, ValueError, KeyError, TypeError):
            reasons.append('definition-request-unavailable')
            facts.append(SemanticFact(kind, source, None, 'unknown', callsite,
                                      'Selected definition analysis is unavailable.'))
    return tuple(facts), resolved, tuple(sorted(set(reasons)))


def _cpp_callable_targets(contents):
    """Index exact declaring function names, never a containing method or a same-name guess."""
    from .flow_languages.cpp_support.declarations import function_declarator
    from .semantic_sources import SourceCoordinates
    from tree_sitter import Language, Parser
    import tree_sitter_cpp
    parser = Parser(Language(tree_sitter_cpp.language()))
    names, coordinates, visited = {}, {}, 0
    for path, text in contents.items():
        content = text.encode('utf-8')
        tree = parser.parse(content)
        coordinates[path] = SourceCoordinates(content)
        names[path] = []
        pending = [tree.root_node] if tree is not None else []
        while pending and visited < 20000:
            node = pending.pop()
            visited += 1
            if node.type in {'function_definition', 'declaration', 'field_declaration'}:
                declarator = function_declarator(node)
                name = declarator.child_by_field_name('declarator') if declarator else None
                if name is not None and name.type in {'identifier', 'field_identifier', 'qualified_identifier',
                                                     'operator_name', 'destructor_name', 'template_function'}:
                    names[path].append((name.start_byte, name.end_byte))
            pending.extend(reversed(node.named_children))

    def is_callable(target):
        """Accept a compiler-selected range only when it lies within a declaration's own name."""
        span = target.source_span
        if span is None or span.path not in coordinates:
            return False
        start, end = coordinates[span.path].span_bytes(span)
        return any(left <= start and end <= right for left, right in names[span.path])

    return is_callable


def _compile_entries(path, root):
    """Read bounded existing compilation metadata as data, never shell commands."""
    with path.open('rb') as handle:
        encoded = handle.read(1024 * 1024 + 1)
    if len(encoded) > 1024 * 1024:
        raise ValueError('Compilation database exceeds budget')
    entries = json.loads(encoded)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 2048:
        raise ValueError('Invalid compilation database')
    normalized = {}
    for entry in entries:
        directory = Path(entry['directory'])
        if not directory.is_absolute():
            directory = root / directory
        filename = Path(entry['file'])
        filename = filename if filename.is_absolute() else directory / filename
        try:
            relative = filename.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        arguments = entry.get('arguments')
        if arguments is None:
            arguments = shlex.split(entry['command'])
        if (not isinstance(arguments, list) or not 1 <= len(arguments) <= 256
                or any(not isinstance(value, str) or len(value) > 4096 for value in arguments)):
            raise ValueError('Invalid compilation arguments')
        normalized.setdefault(relative, []).append((directory, arguments))
    return normalized


def _safe_compile_flags(directory, arguments, root, workspace):
    """Copy a small allowlist of parser flags; reject plugins/response files/unknown semantics."""
    result, index = [], 1
    while index < len(arguments):
        flag = arguments[index]
        if flag in {'-o', '-MF', '-MT', '-MQ'}:
            index += 2
            continue
        if flag in {'-c', '-MD', '-MMD', '-MP', '-g', '-w'} or re.fullmatch(r'-[OW][\w=+,-]*', flag):
            index += 1
            continue
        if flag in {'-I', '-isystem', '-iquote'} or flag.startswith('-I'):
            if flag in {'-I', '-isystem', '-iquote'}:
                index += 1
                include = arguments[index]
                prefix = flag
            else:
                include, prefix = flag[2:], '-I'
            path = Path(include)
            path = path if path.is_absolute() else directory / path
            relative = path.resolve().relative_to(root.resolve())
            result.extend([prefix, str(workspace / relative)])
        elif flag in {'-D', '-U', '-std', '-x'}:
            index += 1
            value = arguments[index]
            if flag == '-x' and value != 'c++':
                raise ValueError('Only the existing cpp profile is supported')
            result.extend([flag, value])
        elif (flag.startswith(('-D', '-U', '-std=', '--target=')) or flag in {
                '-m32', '-m64', '-pthread', '-fno-exceptions', '-fexceptions', '-fno-rtti', '-frtti',
                '-fms-extensions', '-fms-compatibility', '-funsigned-char', '-fsigned-char', '-fshort-wchar'}):
            result.append(flag)
        elif not flag.startswith(('-', '@')) and Path(flag).suffix in {'.cpp', '.cc', '.cxx', '.h', '.hpp'}:
            pass
        else:
            raise ValueError('Unsupported or executable compilation flag')
        index += 1
    return result


def _cpp_include_closure_complete(contents, compilation, workspace):
    """Prove a bounded pinned include closure before trusting overload selection.

    Missing headers can add better overloads even when clangd returns one local
    definition. We deliberately do not search the original repository or wait
    for asynchronous diagnostics: an unproven include makes every fact candidate.
    """
    from tree_sitter import Language, Parser
    import tree_sitter_cpp
    parser = Parser(Language(tree_sitter_cpp.language()))
    include_roots = []
    for entry in compilation:
        arguments = entry['arguments']
        roots = [Path(arguments[index + 1]) for index, flag in enumerate(arguments[:-1])
                 if flag in {'-I', '-iquote', '-isystem'}]
        include_roots.append(roots)
    visited = 0
    for path, text in contents.items():
        content = text.encode('utf-8')
        tree = parser.parse(content)
        if tree.root_node.has_error or '__has_include' in text:
            return False
        pending = [tree.root_node]
        while pending:
            node = pending.pop()
            visited += 1
            if visited > 20000:
                return False
            if node.type == 'preproc_include':
                name = node.child_by_field_name('path')
                if name is None or name.type not in {'string_literal', 'system_lib_string'}:
                    return False
                spelling = content[name.start_byte:name.end_byte].decode('utf-8')
                if '\\' in spelling:
                    return False
                for roots in include_roots:
                    candidates = ([workspace / Path(path).parent] if spelling.startswith('"') else []) + roots
                    if not candidates:
                        return False
                    # Only the first search location is proved without reading unpinned files.
                    target = candidates[0] / spelling[1:-1]
                    try:
                        relative = target.resolve().relative_to(workspace.resolve()).as_posix()
                    except ValueError:
                        return False
                    if relative not in contents:
                        return False
            elif node.type == 'preproc_call':
                return False
            pending.extend(node.named_children)
    return bool(compilation)


class ClangdProvider:
    """Use an existing clangd and safe compile metadata for selected pinned C++ bindings."""
    language = 'cpp'

    def __init__(self, provider_id, *, engine_path=None):
        """An explicit host choice has precedence and never falls back to another executable."""
        self.provider_id, self.engine_path = provider_id, engine_path

    def _candidates(self):
        """Inspect known system installations only; no recursive SDK/editor search."""
        if self.engine_path:
            selected = discover_executable('clangd', explicit=self.engine_path)
            return [selected] if selected else []
        candidates = [discover_executable('clangd')]
        candidates.extend(Path(path) for path in ('/opt/homebrew/opt/llvm/bin/clangd',
                                                 '/usr/local/opt/llvm/bin/clangd') if Path(path).is_file())
        return list(dict.fromkeys(path for path in candidates if path))

    def probe(self, context, cancel):
        """Require real clangd compatibility plus an existing nonempty compilation target."""
        root = Path(context.workspace_root)
        database = root / 'compile_commands.json'
        candidates = self._candidates()
        if not candidates:
            return ProbeOutcome(SemanticAvailability('missing', 'clangd-not-installed'))
        try:
            entries = _compile_entries(database, root)
            applicable = [path for path in entries if path in context.source_hashes]
            if not applicable:
                raise ValueError('No captured compilation target')
            for path in applicable:
                if len(entries[path]) != 1:
                    raise ValueError('Ambiguous compilation target')
                directory, arguments = entries[path][0]
                _safe_compile_flags(directory, arguments, root, root)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return ProbeOutcome(SemanticAvailability('incompatible', 'context-missing-or-unsupported-compile-target'))
        for engine in candidates[:3]:
            try:
                result = run_bounded([str(engine), '--version'], cwd=root, cancel=cancel, timeout=3,
                                     max_output_bytes=8192)
                version = result.stdout.decode('utf-8', errors='replace').strip()
                match = re.search(r'clangd version (\d+)', version)
                if result.returncode or not match or not 14 <= int(match.group(1)) <= 23:
                    continue
                assets = (engine, database, Path(__file__).resolve(),
                          Path(__file__).with_name('semantic_lsp.py').resolve())
                return ProbeOutcome(SemanticAvailability('available', engine_version=version[:256],
                    engine_fingerprint=_fingerprint(assets, version), capabilities=('definition', 'calls')), assets)
            except (SemanticProcessError, OSError):
                continue
        return ProbeOutcome(SemanticAvailability('incompatible', 'clangd-version-or-runtime-unavailable'))

    def analyze(self, context, probe, request, cancel):
        """Stage captured text and safe compile flags, then resolve only the selected call sites."""
        contents, queries = _request_sources(context, request)
        root = Path(context.workspace_root)
        entries = _compile_entries(probe.assets[1], root)
        with tempfile.TemporaryDirectory(prefix='atlas-clangd-') as directory:
            workspace = Path(directory)
            for path, text in contents.items():
                destination = workspace / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding='utf-8')
            compilation = []
            for path in contents:
                if path not in entries:
                    continue
                if len(entries[path]) != 1:
                    raise ValueError('Ambiguous compilation target')
                original_directory, arguments = entries[path][0]
                flags = _safe_compile_flags(original_directory, arguments, root, workspace)
                compilation.append({'directory': str(workspace), 'file': str(workspace / path),
                    'arguments': ['clang++', '-nostdinc', '-nostdinc++', *flags, '-c', str(workspace / path)]})
            (workspace / 'compile_commands.json').write_text(json.dumps(compilation), encoding='utf-8')
            context_complete = _cpp_include_closure_complete(contents, compilation, workspace)
            command = [str(probe.assets[0]), '--background-index=false', '--clang-tidy=false',
                       '--enable-config=false', '--query-driver=', '-j=1', '--log=error',
                       f'--compile-commands-dir={workspace}']
            with LspSession(command, workspace, cancel) as session:
                session.initialize(workspace)
                for path, text in contents.items():
                    session.notify('textDocument/didOpen', {'textDocument': {
                        'uri': (workspace / path).as_uri(), 'languageId': 'cpp', 'version': 1, 'text': text}})
                facts, resolved, reasons = _definition_facts(session, workspace, context,
                    self.provider_id, contents, queries, callable_target=_cpp_callable_targets(contents))
            if not context_complete:
                facts = tuple(replace(fact, confidence='candidate', evidence='include-closure-not-proven')
                              if fact.confidence == 'confirmed-static' else fact for fact in facts)
                resolved = 0
                reasons += ('include-closure-not-proven',)
            coverage = SemanticCoverage('partial', resolved, len(queries),
                tuple(sorted(set(reasons + ('selected-pinned-scope-only', 'external-headers-not-loaded')))))
            return SemanticResult(context, self.provider_id, context.provider_fingerprint,
                probe.availability.engine_fingerprint, 'partial', facts,
                {'definition': coverage, 'calls': SemanticCoverage('partial',
                    sum(fact.kind == 'call' and fact.confidence == 'confirmed-static' for fact in facts),
                    sum(query.get('kind') == 'call' for query in queries), coverage.reasons)})


def create_lsp_provider(provider_id, language, *, engine_path=None):
    """Select only implemented host profiles; absent optional adapters use the factory fallback."""
    if language == 'cpp':
        return ClangdProvider(provider_id, engine_path=engine_path)
    if language == 'go':
        from .semantic_providers.gopls import GoplsProvider
        return GoplsProvider(provider_id, engine_path=engine_path)
    if language == 'rust':
        from .semantic_providers.rust_analyzer import RustAnalyzerProvider
        return RustAnalyzerProvider(provider_id, engine_path=engine_path)
    if language == 'swift':
        from .semantic_providers.sourcekit import SourceKitProvider
        return SourceKitProvider(provider_id, engine_path=engine_path)
    if language == 'java':
        from .semantic_providers.jdt import JdtProvider
        return JdtProvider(provider_id, engine_path=engine_path)
    if language == 'kotlin':
        from .semantic_providers.kotlin import KotlinProvider
        return KotlinProvider(provider_id, engine_path=engine_path)
    raise ImportError('Optional LSP profile is not available in this host')
