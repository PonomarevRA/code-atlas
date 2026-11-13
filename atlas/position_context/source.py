"""Position ownership from bounded existing request-local language analysis."""

import ast
from pathlib import Path

from ..execution_path_source import SourceAnalysis
from ..flow_languages.parsed_source import parsed_tree
from ..source_viewer import SourceError, _source_path, read_node_source
from ..source_provenance import source_provenance
from .models import PositionContextError

MAX_DESCRIPTIONS = 120


def relative_name(root, value):
    """Normalize captured path syntax without resolving thousands of source paths."""
    if not isinstance(value, str):
        return None
    path = Path(value.replace('\\', '/'))
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            return None
    # Lexical normalization is for matching only; the existing safe reader owns IO.
    import posixpath
    name = posixpath.normpath(path.as_posix())
    return name if name != '..' and not name.startswith('../') else None


def callable_node(node):
    """Ignore field/control/diagnostic nodes when resolving declaration ownership."""
    return (node.get('kind') in ('function', 'method', 'constructor', 'class', 'interface', 'struct', 'record')
            or (node.get('memberFacts') or {}).get('kind') in ('method', 'function', 'constructor', 'class')
            or '(' in str(node.get('label', '')))


def inventory(analysis, filename, line):
    """Reuse parsed C#, Python and JavaScript trees with byte containment for ambiguity.

    Class containers own their declaration line only; a blank gap between methods
    is not silently attributed to the surrounding class. Unknown grammars and
    exhausted AST budgets return None, never a prefix as complete evidence.
    """
    loaded = analysis.files.get(filename)
    result = []
    if isinstance(loaded, tuple):
        _, content, declarations, _, _, _ = loaded
        for item in declarations:
            if item.has_error:
                return None
            name = item.child_by_field_name('name')
            kind = 'class' if item.type in {'class_declaration', 'interface_declaration', 'struct_declaration', 'record_declaration'} else 'method'
            result.append({'line': item.start_point.row + 1, 'endLine': item.end_point.row + 1,
                           'start': item.start_byte, 'end': item.end_byte, 'kind': kind,
                           'name': content[name.start_byte:name.end_byte].decode('utf-8') if name else ''})
        return result
    if isinstance(loaded, dict) and filename.lower().endswith('.py'):
        try:
            tree = parsed_tree(loaded, 'python', lambda data: ast.parse(loaded['text']))
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return None
        offsets = [0]
        for line in loaded['text'].encode('utf-8').splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        pending, count = [tree], 0
        while pending:
            item = pending.pop(); count += 1
            if count > 20000:
                return None
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                first = min([item.lineno, *[d.lineno for d in item.decorator_list]])
                result.append({'line': first, 'declarationLine': item.lineno, 'endLine': item.end_lineno,
                               'start': offsets[item.lineno - 1] + item.col_offset,
                               'end': offsets[item.end_lineno - 1] + item.end_col_offset,
                               'kind': 'class' if isinstance(item, ast.ClassDef) else 'method', 'name': item.name})
            pending.extend(ast.iter_child_nodes(item))
        return result
    if isinstance(loaded, dict) and Path(filename).suffix.lower() in {'.js', '.jsx', '.mjs', '.cjs'}:
        from ..flow_languages.javascript import callable_declarations
        from ..flow_languages.javascript_support.declarations import anchor_lines, name_of
        content, declarations, _ = callable_declarations(loaded, line=line)
        if declarations is None:
            return None
        return [{'line': item.start_point.row + 1, 'endLine': item.end_point.row + 1,
                 'start': item.start_byte, 'end': item.end_byte, 'kind': 'method',
                 'columns': (item.start_point.column + 1, item.end_point.column + 1),
                 'anchorLines': anchor_lines(item),
                 'name': name_of(item, content)} for item in declarations]
    return None


def _source_metadata(analysis, filename, root, snapshot):
    """Obtain safe-read byte identity from the same cached source used for parsing."""
    loaded = analysis.files.get(filename)
    if isinstance(loaded, dict):
        return {key: loaded.get(key) for key in ('file', 'sourceKind', 'sourceByteHash', 'sourceReadAt', 'totalLines')}
    if isinstance(loaded, tuple):
        return {'file': loaded[0], 'sourceKind': 'working-tree', 'sourceByteHash': loaded[4], 'sourceReadAt': loaded[5],
                'totalLines': loaded[1].replace(b'\r\n', b'\n').replace(b'\r', b'\n').count(b'\n') + 1}
    source = read_node_source(root, {'file': filename}, snapshot, include_syntax=False)
    # The initial probe may not support this grammar. Its safe-read fallback
    # still consumes the shared unique-file byte allowance before caller reads.
    analysis.total_bytes = max(analysis.total_bytes, len(source['text'].encode('utf-8')))
    return source


def resolve(root, graph, body, nodes):
    """Resolve all innermost declarations without nearest-preceding or label-only guesses."""
    try:
        root, relative = _source_path(root, {'file': body.file})
        filename = relative.as_posix()
        requested = relative_name(root, body.file)
        file_nodes = [node for node in nodes.values()
                      if relative_name(root, node.get('file') or node.get('source_file')) in {filename, requested}]
        if not file_nodes:
            raise PositionContextError('File is not represented in the selected graph', 404, 'position_file_unknown')
        candidates = [node for node in file_nodes if callable_node(node)]
        if Path(filename).suffix.lower() in {'.html', '.htm'}:
            # Inline JavaScript ranges belong to the HTML snapshot and are verified
            # against its whole-file hash below. Unrelated functions cost no descriptions.
            candidates = [node for node in candidates if captured_inline_range(node, body.line)]
        analysis = SourceAnalysis(root, body.snapshotId, 5, 1024 * 1024, 24)
        analysis.describe({'id': '__position_probe__', 'file': filename, 'line': body.line,
                           'label': '__position_probe__()', 'kind': 'function'})
        current = _source_metadata(analysis, filename, root, body.snapshotId)
    except SourceError as error:
        raise PositionContextError(str(error), error.status, 'position_source_unavailable') from error
    current_hash = current.get('sourceByteHash')
    if body.expectedSourceHash and body.expectedSourceHash.lower() != current_hash:
        raise PositionContextError('Current source differs from expectedSourceHash', 409, 'position_source_changed')
    ranges = inventory(analysis, filename, body.line)
    matches = [] if ranges is None else [item for item in ranges if item['line'] <= body.line <= item['endLine']
                                       and (item['kind'] != 'class' or body.line <= item.get('declarationLine', item['line']))]
    inner = [item for item in matches if not any(
        item['start'] <= other['start'] and item['end'] >= other['end']
        and (item['start'], item['end']) != (other['start'], other['end']) for other in matches)]
    if ranges and 'anchorLines' in ranges[0]:
        lines = {line for item in inner for line in item['anchorLines']}
        candidates = [node for node in candidates if (node.get('line') or node.get('source_line')) in lines]
    descriptions, eligible = {}, []
    for node in candidates[:MAX_DESCRIPTIONS]:
        copy = dict(node, file=filename)
        description = analysis.describe(copy)
        provenance = source_provenance(graph, node, {**description, **current}, body.snapshotId)
        descriptions[node['id']] = description
        if ranges is not None:
            raw_name = (node.get('memberFacts') or {}).get('name') or str(node.get('label', '')).strip('.').split('(', 1)[0]
            selected = [item for item in inner if (description.get('line'), description.get('endLine')) == (item.get('declarationLine', item['line']), item['endLine'])
                        and ('columns' not in item or (description.get('column'), description.get('endColumn')) == item['columns'])
                        and raw_name == item['name'] and description.get('sourceStatus') == 'parsed']
        else:
            # Only explicitly source-backed captured ranges may substitute for AST.
            selected = []
            for key in ('callableFacts', 'declarationFacts'):
                facts = node.get(key) or {}
                start, end = facts.get('line'), facts.get('endLine')
                if (facts.get('sourceKind') in {'snapshot-captured-source', 'snapshot-captured-ast'}
                        and facts.get('sourceHash') == current_hash and type(start) is int and type(end) is int
                        and start <= body.line <= end):
                    selected.append({'line': start, 'endLine': end, 'start': start, 'end': end})
                    if key == 'callableFacts' and facts.get('scriptNodeId'):
                        selected[-1]['scriptRange'] = (facts['scriptNodeId'], facts.get('scriptStartByte'),
                                                       facts.get('scriptEndByte'))
        if selected:
            eligible.append({'id': node['id'], 'line': selected[0]['line'], 'endLine': selected[0]['endLine'],
                             'rangeKind': 'current-ast' if ranges is not None else 'captured-range', 'sourceProvenance': provenance})
            if 'scriptRange' in selected[0]:
                eligible[-1]['scriptRange'] = selected[0]['scriptRange']
    if ranges is None:
        eligible = [item for item in eligible if not any(contains_range(item, other) for other in eligible)]
        for item in eligible:
            item.pop('scriptRange', None)
    limited = len(candidates) > MAX_DESCRIPTIONS
    if body.line > current.get('totalLines', body.line) or ranges is not None and not inner:
        eligible = []
        status = 'not-found'
    elif limited or ranges is None and not eligible:
        status = 'unknown'
    elif len(inner) > 1 or len(eligible) > 1:
        status = 'ambiguous'
    elif not eligible:
        status = 'unknown'
    else:
        status = 'resolved' if eligible[0]['sourceProvenance']['status'] == 'match' else 'unknown'
    anchor = eligible[0]['id'] if len(eligible) == 1 and not limited and len(inner) <= 1 else None
    provenance = eligible[0]['sourceProvenance'] if anchor else source_provenance(graph, {}, current, body.snapshotId)
    return {'status': status, 'anchorId': anchor, 'candidates': eligible,
            'sourceProvenance': provenance, 'file': filename,
            'sourceEvidence': {**descriptions.get(anchor, {}),
                               **(literal_nulls(analysis, filename, descriptions.get(anchor, {})) if anchor else {})},
            'astCandidates': [{key: item[key] for key in ('name', 'kind', 'line', 'endLine')} for item in inner[:40]],
            'astCandidatesTruncated': len(inner) > 40,
            '_analysis': analysis, '_descriptions': descriptions,
            'descriptionLimitReached': limited, 'astAvailable': ranges is not None,
            'analysisTruncation': analysis.truncation}


def captured_inline_range(node, line):
    """Select only complete AST ranges that can own this line in the HTML snapshot."""
    facts = node.get('callableFacts') or {}
    start, end = facts.get('line'), facts.get('endLine')
    return (facts.get('sourceKind') == 'snapshot-captured-ast'
            and type(start) is int and type(end) is int and start <= line <= end)


def contains_range(outer, inner):
    """Compare script-local byte offsets, or line ranges for other captured languages."""
    first, second = outer.get('scriptRange'), inner.get('scriptRange')
    if first or second:
        if not first or not second or first[0] != second[0]:
            return False
        outer_bounds, inner_bounds = first[1:], second[1:]
        if any(type(bound) is not int for bound in (*outer_bounds, *inner_bounds)):
            return False
    else:
        outer_bounds = outer['line'], outer['endLine']
        inner_bounds = inner['line'], inner['endLine']
    return outer_bounds != inner_bounds and outer_bounds[0] <= inner_bounds[0] and outer_bounds[1] >= inner_bounds[1]


def literal_nulls(analysis, filename, description):
    """Observe direct AST null/None arguments, excluding nested/deferred bodies and aliases."""
    loaded = analysis.files.get(filename)
    start, end = description.get('line'), description.get('endLine')
    result = []
    unavailable = {'literalNullArguments': [], 'literalNullStatus': 'unavailable', 'literalNullArgumentsTruncated': False}
    if isinstance(loaded, dict) and filename.endswith('.py'):
        from ..flow_languages.python_support.source import Source
        tree = parsed_tree(loaded, 'python', lambda data: ast.parse(loaded['text']))
        matches = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.lineno == start and node.end_lineno == end]
        if len(matches) != 1:
            return unavailable
        source, declaration = Source(loaded['text']), matches[0]
        pending = [declaration]
        while pending and len(result) < 17:
            node = pending.pop()
            if node is not declaration and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Call):
                arguments = [(arg, None, arg) for arg in node.args]
                arguments.extend((kw.value, kw.arg, kw) for kw in node.keywords)
                # AST separates positional/starred and keyword collections. Legal
                # Python can place a keyword before *args, so restore lexical order.
                arguments.sort(key=lambda record: (record[2].lineno, record[2].col_offset))
                for index, (argument, keyword_name, _) in enumerate(arguments):
                    if isinstance(argument, ast.Constant) and argument.value is None:
                        result.append({'line': argument.lineno, 'column': argument.col_offset + 1,
                                       'callLine': node.lineno,
                                       'callSite': {'line': node.lineno, 'column': node.col_offset + 1,
                                                    'endLine': node.end_lineno, 'endColumn': node.end_col_offset + 1},
                                       'calleeExpression': source.fragment(node.func, 200), 'argumentIndex': index,
                                       'keywordName': keyword_name,
                                       'argumentIndexKind': 'zero-based-source-argument-order; not formal parameter binding',
                                       'literal': 'None', 'evidenceKind': 'working-tree-ast'})
            pending.extend(reversed(list(ast.iter_child_nodes(node))))
    elif isinstance(loaded, tuple):
        _, content, declarations, _, _, _ = loaded
        matches = [node for node in declarations if node.start_point.row + 1 == start and node.end_point.row + 1 == end
                   and node.type in {'method_declaration', 'constructor_declaration', 'local_function_statement'}]
        if len(matches) != 1:
            return unavailable
        declaration = matches[0]; pending = [declaration]
        while pending and len(result) < 17:
            node = pending.pop()
            if node != declaration and node.type in {'method_declaration', 'constructor_declaration', 'local_function_statement',
                                                     'lambda_expression', 'anonymous_method_expression', 'class_declaration'}:
                continue
            if node.type == 'invocation_expression':
                arguments = node.child_by_field_name('arguments')
                function = node.child_by_field_name('function')
                argument_nodes = [item for item in arguments.named_children if item.type == 'argument'] if arguments else []
                for index, argument in enumerate(argument_nodes):
                    if argument.type == 'argument' and any(child.type == 'null_literal' for child in argument.named_children):
                        named = argument.child_by_field_name('name')
                        result.append({'line': argument.start_point.row + 1, 'column': argument.start_point.column + 1,
                                       'callLine': node.start_point.row + 1,
                                       'callSite': {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
                                                    'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1},
                                       'calleeExpression': content[function.start_byte:min(function.end_byte, function.start_byte + 800)].decode('utf-8', errors='ignore') if function else '',
                                       'argumentIndex': index, 'keywordName': content[named.start_byte:named.end_byte].decode('utf-8') if named else None,
                                       'argumentIndexKind': 'zero-based-source-argument-order; not formal parameter binding',
                                       'literal': 'null', 'evidenceKind': 'working-tree-ast'})
            pending.extend(reversed(node.named_children))
    else:
        return unavailable
    return {'literalNullArguments': result[:16], 'literalNullStatus': 'observed', 'literalNullArgumentsTruncated': len(result) > 16}
