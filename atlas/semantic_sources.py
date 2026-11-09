"""Bind optional engine locations to exact, safely read graph-owned declarations."""
from dataclasses import asdict
from hashlib import sha256

from .method_flow_chunks import _parse, _select
from .semantic_models import SourceSpan
from .source_viewer import read_node_source, SourceError
from .source_provenance import _captured_hash

MAX_FILES = 16
MAX_BYTES = 4 * 1024 * 1024
MAX_DECLARATIONS = 256


def parse_graph_source(content, path, node):
    """Use the captured C++ profile for ambiguous headers; do not infer a C language provider."""
    language = ((node.get('memberFacts') or {}).get('language')
                or (node.get('analysisFacts') or {}).get('language')
                or (node.get('codeProjectFacts') or {}).get('language'))
    if language == 'cpp' and path.lower().endswith(('.h', '.hh', '.hxx')):
        return _parse(content, path + '.cpp')
    return _parse(content, path)


def _name_node(declaration):
    """Find the original callable name, including a directly bound JS/TS function value."""
    name = declaration.child_by_field_name('name')
    declarator = declaration.child_by_field_name('declarator')
    for _ in range(24):
        if name is not None or declarator is None:
            break
        nested = declarator.child_by_field_name('declarator')
        if nested is None:
            name = declarator
            break
        declarator = nested
    if name is None and declaration.type in {'arrow_function', 'function_expression', 'generator_function'}:
        parent = declaration.parent
        for _ in range(24):
            if parent is None:
                break
            if parent.type in {'variable_declarator', 'public_field_definition', 'pair'}:
                name = parent.child_by_field_name('name') or parent.child_by_field_name('key')
                break
            if parent.type not in {'parenthesized_expression', 'as_expression', 'satisfies_expression', 'non_null_expression'}:
                break
            parent = parent.parent
    for _ in range(24):
        nested = name.child_by_field_name('name') if name is not None else None
        if nested is None:
            break
        name = nested
    return name


def span_bytes(span: SourceSpan, content: bytes) -> tuple[int, int]:
    """Convert explicit column units to original decoded UTF-8 offsets, rejecting split characters."""
    return SourceCoordinates(content).span_bytes(span)


class SourceCoordinates:
    """Index line starts once per cold analysis, with explicit UTF-8/UTF-16 column conversion."""
    def __init__(self, content):
        """Retain only the already bounded source while building cumulative line byte offsets."""
        # Tree-sitter rows follow LF bytes. Python splitlines() would also split
        # Unicode NEL/separators inside strings and silently move engine spans.
        source_lines = content.decode('utf-8').split('\n')
        self.lines = [line.removesuffix('\r') for line in source_lines]
        self.offsets, position = [], 0
        for line in source_lines:
            self.offsets.append(position)
            position += len(line.encode('utf-8')) + 1

    def offset(self, row, column, coordinate_kind):
        """Find a valid character boundary without rounding an invalid engine coordinate."""
        if row >= len(self.lines):
            raise ValueError('Semantic source row is outside its file')
        line = self.lines[row]
        unit = {'utf-16': 'utf-16-le', 'utf-8': 'utf-8'}.get(coordinate_kind)
        if unit:
            count = column * 2 if coordinate_kind == 'utf-16' else column
            encoded = line.encode(unit)
            if count > len(encoded):
                raise ValueError('Semantic source column is outside its line')
            prefix = encoded[:count].decode(unit)
        else:
            if column > len(line):
                raise ValueError('Semantic source column is outside its line')
            prefix = line[:column]
        return self.offsets[row] + len(prefix.encode('utf-8'))

    def span_bytes(self, span):
        """Convert both endpoints without rescanning preceding file lines."""
        return (self.offset(span.start_line, span.start_column, span.coordinate_kind),
                self.offset(span.end_line, span.end_column, span.coordinate_kind))


def captured_source_hashes(graph: dict) -> dict[str, str]:
    """Keep only unanimous captured file hashes; missing or contradictory provenance stays unknown."""
    hashes, conflicts = {}, set()
    for node in graph.get('nodes', []):
        path = node.get('file') or node.get('source_file')
        digest, conflict = _captured_hash(node)
        if not path or not digest or conflict:
            if conflict and path:
                conflicts.add(path)
            continue
        if path in hashes and hashes[path] != digest:
            conflicts.add(path)
        hashes[path] = digest
    return {path: digest for path, digest in hashes.items() if path not in conflicts}


def declaration_bindings(root, graph, paths, snapshot_id, source_hashes):
    """Parse each selected pinned file once and bind at most 256 existing method declarations.

    Names and saved lines select an original AST declaration; its full byte span
    becomes the identity. Ambiguous same-line methods are never guessed. This
    cold operation runs before publication, never on a warm map read.
    """
    paths = set(paths)
    if len(paths) > MAX_FILES:
        raise ValueError('Semantic source file budget exceeded')
    nodes = [node for node in graph.get('nodes', [])
             if (node.get('file') or node.get('source_file')) in paths]
    contents, bindings, total = {}, [], 0
    for path in sorted(paths):
        representative = next((node for node in nodes if (node.get('file') or node.get('source_file')) == path), None)
        if representative is None or path not in source_hashes:
            continue
        try:
            source = read_node_source(root, representative, snapshot_id, include_syntax=False)
        except SourceError:
            continue
        if source['sourceByteHash'] != source_hashes[path]:
            continue
        content = source['text'].encode('utf-8')
        total += len(content)
        if total > MAX_BYTES:
            raise ValueError('Semantic source byte budget exceeded')
        tree, _ = parse_graph_source(content, path, representative)
        if tree is None:
            continue
        contents[path] = content
        for node in nodes:
            if (node.get('file') or node.get('source_file')) != path:
                continue
            selected, _ = _select(tree.root_node, node, content)
            if selected is None or selected.has_error:
                continue
            span = SourceSpan(path, selected.start_point.row, selected.start_point.column,
                              selected.end_point.row, selected.end_point.column, 'utf-8')
            name = _name_node(selected)
            name_span = SourceSpan(path, name.start_point.row, name.start_point.column,
                                   name.end_point.row, name.end_point.column, 'utf-8') if name is not None else None
            bindings.append({'nodeId': node['id'], 'span': asdict(span),
                             'nameSpan': asdict(name_span) if name_span else None,
                             'sourceHash': source_hashes[path]})
            if len(bindings) >= MAX_DECLARATIONS:
                return contents, bindings
    return contents, bindings


class DeclarationIndex:
    """Index exact declaration ranges once before resolving a bounded set of engine facts."""
    def __init__(self, bindings, contents):
        """Build request-local coordinate/range indexes, never retaining parsed ASTs."""
        self.coordinates = {path: SourceCoordinates(content) for path, content in contents.items()}
        self.ranges = {}
        for binding in bindings:
            span = SourceSpan(**binding['span'])
            if span.path not in self.coordinates:
                continue
            start, end = self.coordinates[span.path].span_bytes(span)
            name = binding.get('nameSpan')
            named = self.coordinates[span.path].span_bytes(SourceSpan(**name)) if name else None
            if named:
                start, end = min(start, named[0]), max(end, named[1])
            self.ranges.setdefault(span.path, []).append((start, end, binding['nodeId'], named))

    def resolve(self, symbol, *, require_callable=False, require_declaration_name=False):
        """Select one smallest containing declaration; equal-span aliases remain ambiguous."""
        span = symbol.source_span
        if span is None or span.path not in self.coordinates:
            return None
        start, end = self.coordinates[span.path].span_bytes(span)
        matches = []
        for left, right, identifier, named in self.ranges.get(span.path, []):
            if not left <= start <= end <= right:
                continue
            if require_callable and symbol.kind.lower() not in {'method', 'function', 'constructor', 'local-function'}:
                continue
            if require_callable or require_declaration_name:
                if named is None:
                    continue
                # Being inside a method proves ownership, not symbol identity.
                # Definitions of local values/types must not become method IDs.
                if not (start < named[1] and named[0] < end or start <= named[0] <= named[1] <= end):
                    continue
            matches.append((right - left, identifier))
        if not matches:
            return None
        smallest = min(size for size, _ in matches)
        targets = {identifier for size, identifier in matches if size == smallest}
        return next(iter(targets)) if len(targets) == 1 else None


def source_fingerprint(contents):
    """Identify decoded text separately from the safe reader's original file-byte hash."""
    return {path: sha256(content).hexdigest() for path, content in contents.items()}
