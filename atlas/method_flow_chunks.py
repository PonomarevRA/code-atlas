"""On-demand syntax pages from original ASTs; these are not method execution trees."""
import base64
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path

from .source_provenance import source_provenance
from .source_viewer import SourceError, read_node_source, MAX_SOURCE_BYTES

VERSION = 'atlas-ast-chunks-v1'
MAX_CHILDREN = 64
MAX_SELECTION_NODES = 20_000
MAX_SELECTION_NAME_SCAN_BYTES = MAX_SOURCE_BYTES * 4
MAX_REF_CHARS = 4096
MAX_LABEL_CHARS = 160
MAX_RESPONSE_BYTES = 64_000
GRAMMARS = {
    '.cs': ('c_sharp', 'language'), '.py': ('python', 'language'),
    '.java': ('java', 'language'), '.js': ('javascript', 'language'),
    '.mjs': ('javascript', 'language'), '.cjs': ('javascript', 'language'),
    '.jsx': ('javascript', 'language'), '.ts': ('typescript', 'language_typescript'),
    '.tsx': ('typescript', 'language_tsx'), '.go': ('go', 'language'),
    '.cpp': ('cpp', 'language'), '.cc': ('cpp', 'language'),
    '.cxx': ('cpp', 'language'), '.hpp': ('cpp', 'language'),
    '.kt': ('kotlin', 'language'), '.php': ('php', 'language_php'),
    '.rs': ('rust', 'language'), '.swift': ('swift', 'language'),
}
DECLARATIONS = {
    'method_declaration', 'constructor_declaration', 'compact_constructor_declaration',
    'function_declaration', 'generator_function_declaration', 'function_definition',
    'method_definition', 'function_item', 'function_signature_item', 'method_signature',
    'abstract_method_signature', 'function_signature', 'protocol_function_declaration',
    'init_declaration', 'secondary_constructor', 'arrow_function', 'function_expression',
    'generator_function', 'lambda_expression', 'anonymous_method_expression',
    'local_function_statement',
}
DEFERRED = DECLARATIONS | {'lambda', 'anonymous_function', 'closure_expression',
    'class_declaration', 'class_definition', 'class', 'interface_declaration',
    'object_declaration', 'anonymous_class'}
WARNING = ('Syntax outline only; execution order, call targets and runtime behaviour are not computed. '
           'The complete bounded source file is parsed on each request; only direct AST children are returned. '
           'Unavailable readability recommendations do not indicate PASS.')


def method_flow_chunk_availability(node: dict) -> dict:
    """Advertise a cheap discovery hint without reading files or probing installed parsers."""
    suffix = Path(str(node.get('file') or node.get('source_file') or '')).suffix.lower()
    supported = suffix in GRAMMARS and node.get('kind') != 'external'
    return {'version': VERSION, 'supported': supported, 'status': 'not-probed' if supported else 'unsupported',
            'requestRequired': supported, 'toolName': 'get_method_flow_chunk',
            'analysisKind': 'syntax-outline', 'flowStatus': 'not-computed', 'warning': WARNING}


def _anchor(node) -> list:
    """Keep reference coordinates independent of native parser object identities."""
    return [node.start_byte, node.end_byte, node.type]


def _span(node) -> dict:
    """Publish zero-based UTF-8 byte positions and an exclusive end against decoded source text."""
    return {'startByte': node.start_byte, 'endByte': node.end_byte,
            'startPoint': {'row': node.start_point.row, 'column': node.start_point.column},
            'endPoint': {'row': node.end_point.row, 'column': node.end_point.column}}


def _label(node, content: bytes) -> str:
    """Decode at most four bytes per display character, even for a multi-megabyte subtree."""
    stop = min(node.end_byte, node.start_byte + MAX_LABEL_CHARS * 4)
    return content[node.start_byte:stop].decode('utf-8', errors='ignore')[:MAX_LABEL_CHARS]


def _reference(identity: dict, selected, current, offset=0) -> str:
    """Create deterministic opaque cursors; checksum is corruption detection, not authorization."""
    payload = {**identity, 'anchor': _anchor(selected), 'current': _anchor(current), 'offset': offset}
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    checksum = sha256(encoded).hexdigest()[:24]
    token = base64.urlsafe_b64encode(encoded).decode().rstrip('=') + '.' + checksum
    if len(token) > MAX_REF_CHARS:
        raise SourceError('Идентификатор узла превышает бюджет AST-ссылки.', 413)
    return token


def _decode_reference(value: str) -> dict:
    """Reject malformed references before they can select any source location."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_REF_CHARS:
        raise SourceError('Некорректная AST-ссылка.', 400)
    try:
        encoded, checksum = value.split('.')
        data = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        if sha256(data).hexdigest()[:24] != checksum:
            raise ValueError('checksum')
        payload = json.loads(data)
        fields = {'version', 'snapshotId', 'nodeId', 'file', 'sourceHash', 'sourceByteHash',
                  'anchor', 'current', 'offset'}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError('fields')
        if type(payload['offset']) is not int or payload['offset'] < 0:
            raise ValueError('offset')
        for key in ('anchor', 'current'):
            span = payload[key]
            if not isinstance(span, list) or len(span) != 3 or type(span[0]) is not int or type(span[1]) is not int:
                raise ValueError('span')
            if span[0] < 0 or span[1] < span[0] or not isinstance(span[2], str) or len(span[2]) > 120:
                raise ValueError('span')
        for key in fields - {'anchor', 'current', 'offset'}:
            if not isinstance(payload[key], str):
                raise ValueError('identity')
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError) as error:
        raise SourceError('Некорректная AST-ссылка.', 400) from error


def _binding_anchor(node):
    """Locate a bound callable's lexical declaration through transparent value wrappers."""
    parent = node.parent
    for _ in range(24):
        if parent is None:
            return node
        if parent.type in {'decorated_definition', 'variable_declarator', 'public_field_definition', 'pair'}:
            return parent
        if parent.type not in {'parenthesized_expression', 'as_expression', 'satisfies_expression', 'non_null_expression'}:
            return node
        parent = parent.parent
    return node


def _name(node, content: bytes) -> str:
    """Read declaration names through C++ declarators and bound JS/TS closures only."""
    if node.type == 'init_declaration':
        return 'init'
    current = node.child_by_field_name('name')
    if current is None:
        declarator = node.child_by_field_name('declarator')
        for _ in range(24):
            if declarator is None:
                break
            nested = declarator.child_by_field_name('declarator')
            if nested is None:
                current = declarator
                break
            declarator = nested
    if current is None and node.type in {'arrow_function', 'function_expression', 'generator_function'}:
        binding = _binding_anchor(node)
        if binding != node:
            current = binding.child_by_field_name('name') or binding.child_by_field_name('key')
    if current is None:
        return ''
    return _label(current, content).strip('`').split('::')[-1]


def _header_intersects(node, row: int) -> bool:
    """A saved declaration line must be in its header, never merely inside its body."""
    start = _binding_anchor(node).start_point.row
    body = node.child_by_field_name('body')
    if body is None and node.type == 'function_declaration':
        # Kotlin's body has no field in the installed grammar.
        for index in range(min(node.named_child_count, MAX_SELECTION_NODES)):
            child = node.named_child(index)
            if child.type == 'function_body':
                body = child
                break
    end = body.start_point.row if body is not None else node.end_point.row
    return start <= row <= end


def _declaration_candidate(node):
    """Follow declaration wrappers whose saved header can precede the callable's first line."""
    if node.type in DECLARATIONS:
        return node
    if node.type == 'decorated_definition':
        definition = node.child_by_field_name('definition')
        return definition if definition is not None and definition.type in DECLARATIONS else None
    if node.type not in {'variable_declarator', 'public_field_definition', 'pair'}:
        return None
    value = node.child_by_field_name('value')
    for _ in range(24):
        if value is None or value.type in DECLARATIONS:
            return value
        if value.type not in {'parenthesized_expression', 'as_expression', 'satisfies_expression', 'non_null_expression'}:
            return None
        value = value.named_child(0)
    return None


def _select(root, raw: dict, content: bytes):
    """Seek only the selected line; an incomplete or ambiguous search never proves identity."""
    line = raw.get('line')
    not_callable = {'file', 'class', 'interface', 'struct', 'enum', 'field', 'property', 'constant', 'event'}
    facts = raw.get('memberFacts') or {}
    if (type(line) is not int or line < 1 or raw.get('kind') in not_callable
            or facts.get('kind') in not_callable or raw.get('_callable_class')):
        return None, 'declaration-anchor-missing'
    row = line - 1
    name = facts.get('name')
    if not name:
        name = str(raw.get('label') or '').lstrip('.').split('(', 1)[0].split('.')[-1].strip('`')
    if not name:
        return None, 'declaration-anchor-missing'
    matches, visited, seen = [], 0, set()
    name_bytes = name.encode('utf-8')
    scanned_bytes = 0
    cursor = root.walk()
    while True:
        current = cursor.node
        visited += 1
        if visited > MAX_SELECTION_NODES:
            return None, 'selection-ast-budget'
        intersects = current.start_point.row <= row <= current.end_point.row
        if intersects:
            candidate = _declaration_candidate(current)
            if (candidate is not None and candidate.id not in seen and _name(candidate, content) == name
                    and _header_intersects(candidate, row)):
                matches.append(candidate)
                seen.add(candidate.id)
            # A matching lexical declaration must contain its name bytes. Bound
            # closures are checked at their variable/property wrapper above,
            # before pruning the value whose name lives outside its own span.
            # This skips large minified call-only bodies without accepting the
            # first match or hiding a nested same-name declaration. Extra byte
            # scanning is itself capped; exhaustion falls back to AST traversal.
            size = current.end_byte - current.start_byte
            if size <= MAX_SELECTION_NAME_SCAN_BYTES - scanned_bytes:
                scanned_bytes += size
                if content.find(name_bytes, current.start_byte, current.end_byte) < 0:
                    intersects = False
            # Native point seeks skip preceding siblings without creating child lists.
            if intersects and cursor.goto_first_child_for_point((row, 0)) is not None:
                continue
        while True:
            if cursor.goto_next_sibling() and cursor.node.start_point.row <= row:
                break
            if not cursor.goto_parent():
                return (matches[0], None) if len(matches) == 1 else (None, 'declaration-missing-or-ambiguous')


def _locate(selected, anchor: list):
    """Resolve an exact native subtree without retaining an index or traversing unrelated bodies."""
    start, end, kind = anchor
    if start < selected.start_byte or end > selected.end_byte:
        raise SourceError('AST-ссылка выходит за пределы выбранного объявления.', 400)
    current = selected.named_descendant_for_byte_range(start, end)
    for _ in range(MAX_SELECTION_NODES):
        if current is None or current.start_byte < selected.start_byte or current.end_byte > selected.end_byte:
            break
        if current.start_byte == start and current.end_byte == end and current.type == kind:
            return current
        current = current.parent
    raise SourceError('Точное поддерево из AST-ссылки отсутствует.', 409)


def _parse(content: bytes, filename: str):
    """Use explicitly bundled grammar modules; never load a parser named by repository content."""
    grammar = GRAMMARS.get(Path(filename).suffix.lower())
    if grammar is None:
        return None, 'unsupported-language'
    try:
        from tree_sitter import Language, Parser
        module = import_module('tree_sitter_' + grammar[0])
        language = Language(getattr(module, grammar[1])())
        return Parser(language).parse(content), None
    except (ImportError, AttributeError):
        return None, 'parser-unavailable'
    except (TypeError, ValueError, RuntimeError):
        return None, 'parser-error'


def build_method_flow_chunk(root: Path, graph: dict, node_id: str, snapshot_id: str,
                            chunk_ref: str | None = None, limit: int = 40, *, parse_cache=None) -> dict:
    """Read one graph-owned source and page immediate children of its exact selected declaration.

    Tokens are bounded, stateless selectors rather than authority. Every request rechecks
    graph membership, the safe reader boundary, original declaration, file and both hashes.
    A stale/cross-node selector raises SourceError; unresolved initial selection is explicit.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CHILDREN:
        raise SourceError(f'Размер страницы AST должен быть от 1 до {MAX_CHILDREN}.', 400)
    raw = next((item for item in graph.get('nodes', []) if item.get('id') == node_id), None)
    if raw is None:
        raise SourceError('Узел не найден в выбранном снимке.', 404)
    token = _decode_reference(chunk_ref) if chunk_ref is not None else None
    if token is not None and (token['version'] != VERSION or token['snapshotId'] != snapshot_id or token['nodeId'] != node_id):
        raise SourceError('AST-ссылка принадлежит другому снимку или узлу.', 409)
    source = read_node_source(root, raw, snapshot_id, include_syntax=False)
    content = source['text'].encode('utf-8')
    identity = {'version': VERSION, 'snapshotId': snapshot_id, 'nodeId': node_id,
                'file': source['file'], 'sourceHash': sha256(content).hexdigest(),
                'sourceByteHash': source['sourceByteHash']}
    if token is not None and any(token[key] != value for key, value in identity.items()):
        raise SourceError('Исходник или путь AST-ссылки изменился; запросите новую страницу.', 409)
    provenance = source_provenance(graph, raw, source, snapshot_id)
    result = {**identity, 'analysisKind': 'syntax-outline', 'flowStatus': 'not-computed',
              'sourceKind': 'working-tree-ast', 'sourceProvenance': provenance,
              'available': False, 'children': [], 'hasMore': False, 'nextRef': None,
              'limits': {'maxChildren': MAX_CHILDREN, 'maxSelectionAstNodes': MAX_SELECTION_NODES,
                         'maxSelectionNameScanBytes': MAX_SELECTION_NAME_SCAN_BYTES,
                         'maxSourceBytes': MAX_SOURCE_BYTES, 'maxRefChars': MAX_REF_CHARS,
                         'maxResponseBytes': MAX_RESPONSE_BYTES,
                         'methodFlowUnchanged': {'maxAstNodes': 20_000, 'maxSteps': 180, 'maxDepth': 24}},
              'coordinateSystem': 'zero-based UTF-8 bytes; exclusive end; decoded UTF-8 source without BOM',
              'warning': WARNING}
    if provenance['status'] == 'changed' or provenance['reason'] == 'captured-hash-conflict':
        result['reason'] = 'snapshot-source-changed' if provenance['status'] == 'changed' else 'captured-hash-conflict'
        return result
    tree, reason = parse_cache(identity, content, source['file']) if parse_cache is not None else _parse(content, source['file'])
    if parse_cache is not None:
        result['warning'] = WARNING.replace('The complete bounded source file is parsed on each request;',
                                             'The complete bounded source file is validated on each request;')
        result['parseResidency'] = 'task-context-idle-cache'
    if tree is None:
        return {**result, 'reason': reason}
    selected, reason = _select(tree.root_node, raw, content)
    if selected is None:
        return {**result, 'reason': reason}
    if token is not None and token['anchor'] != _anchor(selected):
        raise SourceError('Выбранное объявление AST-ссылки не совпадает с узлом карты.', 409)
    current = _locate(selected, token['current']) if token is not None else selected
    offset = token['offset'] if token is not None else 0
    count = current.named_child_count
    if offset > count:
        raise SourceError('Курсор страницы AST выходит за пределы поддерева.', 400)
    children = []
    for index in range(offset, min(count, offset + limit)):
        child = current.named_child(index)
        children.append({'ref': _reference(identity, selected, child), 'type': child.type,
                         'span': _span(child), 'label': _label(child, content),
                         'hasChildren': child.named_child_count > 0,
                         'deferredBoundary': child.type in DEFERRED,
                         'hasParseError': child.has_error})
    parent = current.parent if current != selected else None
    following = offset + len(children)
    result.update(available=True, status='partial' if current.has_error else 'parsed',
                  selectionStatus='working-tree-exact-declaration', currentRef=_reference(identity, selected, current),
                  parentRef=_reference(identity, selected, parent) if parent is not None else None,
                  current={'type': current.type, 'span': _span(current), 'label': _label(current, content),
                           'deferredBoundary': current != selected and current.type in DEFERRED},
                  children=children, childCount=count, pageOffset=offset,
                  hasMore=bool(count), pageHasMore=following < count,
                  nextRef=_reference(identity, selected, current, following) if following < count else None,
                  hasParseError=current.has_error)
    # Complete child descriptors and references are atomic. A byte-limited page
    # resumes at the first omitted child rather than claiming the subtree ended.
    while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_RESPONSE_BYTES:
        if not children:
            raise SourceError('Метаданные страницы AST превышают бюджет ответа.', 413)
        children.pop()
        following = offset + len(children)
        result.update(pageHasMore=following < count,
                      nextRef=_reference(identity, selected, current, following))
    if count > offset and not children:
        raise SourceError('Описание одного узла AST превышает бюджет ответа.', 413)
    return result
