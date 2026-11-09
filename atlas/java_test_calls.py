"""Bounded Java field-receiver call candidates, without class loading or executing tests."""
from collections import defaultdict
from pathlib import Path, PurePosixPath
import re

from .java_annotations import METHODS, TYPES, bind_raw_declarations, parse_java, text
from .source_viewer import SourceError, _read_regular_file

ORIGIN = 'java-source-call-binding'
MAX_FILES = 400
MAX_BYTES = 16 * 1024 * 1024
MAX_AST_NODES = 1_500_000
MAX_DECLARATIONS = 30_000
MAX_CALLS = 20_000
MAX_INHERITANCE = 8
_DEFERRED = TYPES | METHODS | {'lambda_expression', 'class_body'}


def _warn(coverage: dict, message: str) -> None:
    """Mark bounded or unavailable source explicitly without accumulating unbounded messages."""
    coverage['limited'] = True
    if len(coverage['warnings']) < 12:
        coverage['warnings'].append(message)


def _java_files(graph: dict) -> dict:
    """Index relative Java source paths already captured in the graph, rejecting external paths."""
    result = defaultdict(list)
    for node in graph.get('nodes', []):
        if node.get('kind') == 'external':
            continue
        name = str(node.get('file') or node.get('source_file') or '').replace('\\', '/')
        path = PurePosixPath(name)
        if not name.lower().endswith('.java') or path.is_absolute() or '..' in path.parts or ':' in name:
            continue
        result[name].append(node)
    return result


def _read_units(root: Path, files: dict, coverage: dict) -> list:
    """Parse each bounded source once; failed reads and invalid encodings still consume budget."""
    units = []
    for filename, nodes in sorted(files.items()):
        if coverage['attempts'] >= MAX_FILES or coverage['bytes'] >= MAX_BYTES or coverage['astNodes'] >= MAX_AST_NODES:
            _warn(coverage, 'Java field-call source budget reached.')
            break
        coverage['attempts'] += 1
        allowance = min(1024 * 1024, MAX_BYTES - coverage['bytes'])
        coverage['bytes'] += allowance
        try:
            content = _read_regular_file(root, Path(filename), allowance)
            coverage['bytes'] -= allowance - len(content)
            ast_allowance = min(100_000, MAX_AST_NODES - coverage['astNodes'])
            coverage['astNodes'] += ast_allowance
            unit = parse_java(content, filename, set(), ast_allowance)
            coverage['astNodes'] -= ast_allowance - unit.inspected_nodes
        except (SourceError, OSError, UnicodeError, ImportError, ValueError, TypeError):
            _warn(coverage, 'A Java field-call source file could not be parsed safely.')
            continue
        if unit.truncated or unit.parse_errors:
            _warn(coverage, 'Partial Java source excluded from field-call binding.')
            continue
        if coverage['declarations'] + len(unit.declarations) > MAX_DECLARATIONS:
            _warn(coverage, 'Java field-call declaration budget reached.')
            break
        bind_raw_declarations(unit, nodes)
        coverage['declarations'] += len(unit.declarations)
        coverage['files'] += 1
        units.append(unit)
    return units


def _indexes(units: list, nodes: list[dict]) -> dict:
    """Build unambiguous class, method and field indexes before inspecting any call sites."""
    types, methods, fields = defaultdict(list), defaultdict(list), defaultdict(list)
    records = []
    for unit in units:
        for record in unit.declarations:
            if not record.get('nodeId'):
                continue
            records.append(record)
            if record['kind'] in TYPES:
                types[record['qualifiedName']].append(record)
            elif record['kind'] in METHODS:
                owner = record['qualifiedName'].rsplit('.', 1)[0]
                methods[(owner, record['name'], len(record['parameters']))].append(record)
    classes = {name: values[0] for name, values in types.items() if len(values) == 1}
    by_id = {record['nodeId']: record for record in classes.values()}
    for node in nodes:
        facts = node.get('fieldFacts') or {}
        member = node.get('memberFacts') or {}
        if facts.get('kind') == 'field' and member.get('language') == 'java':
            fields[(member.get('ownerNodeId'), facts.get('name'))].append(node)
    return {'classes': classes, 'classNames': set(types), 'byId': by_id, 'methods': methods,
            'fields': fields, 'records': records, 'rawById': {node['id']: node for node in nodes},
            'chains': {}, 'incompleteChains': set()}


def _class_chain(owner: dict, index: dict) -> list[dict]:
    """Follow only unique source superclasses; unresolved, generic and cyclic bases stop lookup."""
    identity = owner['nodeId']
    if identity in index['chains']:
        return index['chains'][identity]
    result, seen = [], set()
    while owner:
        if owner['qualifiedName'] in seen or len(result) >= MAX_INHERITANCE or not _source_current(owner, index):
            index['incompleteChains'].add(identity)
            break
        result.append(owner)
        seen.add(owner['qualifiedName'])
        base = owner.get('base')
        if not base:
            break
        if not re.fullmatch(r'[A-Za-z_][\w.$]*', base):
            index['incompleteChains'].add(identity)
            break
        qualified, resolution = owner['unit'].resolve(base, index['classNames'], owner['owner'])
        if resolution not in {'qualified', 'import', 'package', 'enclosing-type'}:
            index['incompleteChains'].add(identity)
            break
        owner = index['classes'].get(qualified)
        if owner is None:
            index['incompleteChains'].add(identity)
    index['chains'][identity] = result
    return result


def _field(receiver: str, owner: dict, index: dict) -> dict | None:
    """Nearest declaration shadows a base field; ambiguous or inaccessible inherited fields stop lookup."""
    for position, candidate in enumerate(_class_chain(owner, index)):
        fields = index['fields'].get((candidate['nodeId'], receiver), [])
        if not fields:
            continue
        if len(fields) != 1:
            return None
        field = fields[0]
        if position:
            visibility = (field.get('memberFacts') or {}).get('visibility')
            if visibility == 'private':
                return None
            if visibility == 'package-private' and candidate['unit'].package != owner['unit'].package:
                return None
        return field
    return None


def _calls(record: dict, coverage: dict) -> tuple[list, set]:
    """Collect ordinary receiver calls and local shadows, excluding deferred/local-class bodies."""
    body = record['_ast'].child_by_field_name('body')
    pending, calls = [body] if body else [], []
    shadows = {parameter['name'] for parameter in record['parameters']}
    while pending:
        node = pending.pop()
        coverage['inspectedBodyNodes'] += 1
        if coverage['inspectedBodyNodes'] > MAX_AST_NODES:
            _warn(coverage, 'Java field-call body inspection budget reached.')
            break
        if node is not body and node.type in _DEFERRED:
            continue
        if node.type in {'variable_declarator', 'formal_parameter', 'catch_formal_parameter',
                         'enhanced_for_statement', 'instanceof_expression'}:
            shadows.add(text(node.child_by_field_name('name'), record['unit'].content))
        if node.type in {'type_pattern', 'record_pattern_component'}:
            for child in node.named_children:
                if child.type == 'identifier':
                    shadows.add(text(child, record['unit'].content))
        if node.type == 'method_invocation':
            receiver = node.child_by_field_name('object')
            if receiver is not None and receiver.type == 'identifier':
                calls.append(node)
        pending.extend(reversed(node.named_children))
    return calls, shadows


def _source_current(record: dict, index: dict) -> bool:
    """Do not connect declarations whose captured annotation/member source differs from this read."""
    raw = index['rawById'][record['nodeId']]
    for name in ('attributeFacts', 'callableFacts', 'typeFacts'):
        stamp = (raw.get(name) or {}).get('sourceHash')
        if stamp and stamp != record['unit'].source_hash:
            return False
    return True


def _declared_field_type(owner: dict, name: str, index: dict) -> str | None:
    """Resolve ordinary declared types; nested/inherited type shadows stay outside this narrow binder."""
    if not re.fullmatch(r'[A-Za-z_][\w.$]*', name):
        return None
    head = name.split('.', 1)[0]
    scope = '.'.join(part for part in (owner['owner'], owner['name']) if part)
    parts = scope.split('.')
    while parts:
        nested = '.'.join(part for part in (owner['unit'].package, '.'.join(parts), head) if part)
        if nested in index['classNames']:
            return None
        parts.pop()
    for base in _class_chain(owner, index)[1:]:
        if base['qualifiedName'] + '.' + head in index['classNames']:
            return None
    qualified, resolution = owner['unit'].resolve(name, index['classNames'], scope)
    if resolution not in {'qualified', 'import', 'package', 'enclosing-type'}:
        return None
    return qualified


def _target(record: dict, call, shadows: set, index: dict) -> tuple[dict, dict] | None:
    """Bind a simple unshadowed field receiver to one source class/method/arity candidate."""
    unit = record['unit']
    receiver = text(call.child_by_field_name('object'), unit.content)
    if receiver in shadows:
        return None
    owner = index['classes'].get(record['qualifiedName'].rsplit('.', 1)[0])
    if owner is None:
        return None
    field = _field(receiver, owner, index)
    if field is None:
        return None
    field_owner = index['byId'].get((field.get('memberFacts') or {}).get('ownerNodeId'))
    if field_owner is None:
        return None
    field_facts = field['fieldFacts']
    if field_facts.get('sourceHash') and field_facts['sourceHash'] != field_owner['unit'].source_hash:
        return None
    name = field_facts.get('type', '')
    qualified = _declared_field_type(field_owner, name, index)
    if qualified is None:
        return None
    target_type = index['classes'].get(qualified)
    if target_type is None or target_type['kind'] == 'interface_declaration':
        return None
    arguments = call.child_by_field_name('arguments')
    arity = len(arguments.named_children) if arguments else 0
    method = text(call.child_by_field_name('name'), unit.content)
    candidates = []
    for candidate_type in _class_chain(target_type, index):
        candidates.extend(index['methods'].get((candidate_type['qualifiedName'], method, arity), []))
    if target_type['nodeId'] in index['incompleteChains']:
        return None
    if len(candidates) != 1:
        return None
    target = candidates[0]
    if not _source_current(target, index):
        return None
    visibility = (index['rawById'][target['nodeId']].get('memberFacts') or {}).get('visibility')
    caller_owner = record['qualifiedName'].rsplit('.', 1)[0]
    target_owner = target['qualifiedName'].rsplit('.', 1)[0]
    if target_owner != caller_owner and visibility == 'private':
        return None
    if unit.package != target['unit'].package and visibility in {'package-private', 'protected'}:
        return None
    if target['_ast'].child_by_field_name('body') is None:
        return None
    return target, field


def _edge(record: dict, call, target: dict, field: dict) -> dict:
    """Publish an inferred source call with exact location; declared types do not prove runtime dispatch."""
    unit, line = record['unit'], call.start_point.row + 1
    return {'source': record['nodeId'], 'target': target['nodeId'], 'relation': 'calls',
        'confidence': 'INFERRED', '_origin': ORIGIN, 'source_file': record['file'],
        'source_location': f'L{line}', 'lineKind': 'snapshot-captured-ast',
        'evidence': {'kind': 'typed-java-field-call', 'sourceKind': 'snapshot-captured-source',
            'file': record['file'], 'line': line, 'bindingFile': field['file'], 'bindingLine': field['line'],
            'receiverType': field['fieldFacts']['type'], 'declaringType': target['qualifiedName'].rsplit('.', 1)[0],
            'callee': target['name'], 'expression': text(call, unit.content),
            'sourceHash': unit.source_hash, 'runtimeSelectionKnown': False, 'ambiguous': False}}


def enrich_java_test_calls(graph: dict, root: Path, rules: dict) -> dict:
    """Add bounded field-call candidates for enabled Java test providers, preserving all raw facts."""
    edges = [edge for edge in graph.get('edges', []) if edge.get('_origin') != ORIGIN]
    coverage = {'files': 0, 'attempts': 0, 'bytes': 0, 'astNodes': 0, 'declarations': 0,
        'inspectedBodyNodes': 0, 'callSites': 0, 'derivedCalls': 0, 'unresolvedCalls': 0,
        'limited': False, 'warnings': []}
    active = any(framework.get('language') == 'java' for framework in rules.get('testFrameworks', []))
    metadata = {**(graph.get('metadata') or {})}
    facts = {'version': 'java-field-calls-v1', 'status': 'disabled', 'coverage': coverage,
        'analysisFingerprint': rules.get('fingerprint'),
        'warning': 'Source field receiver candidates only; dynamic dispatch, overload types and execution are unknown.'}
    metadata['javaTestCalls'] = facts
    if not active:
        return {**graph, 'edges': edges, 'metadata': metadata}
    units = _read_units(Path(root).resolve(), _java_files(graph), coverage)
    index = _indexes(units, graph.get('nodes', []))
    known_edges = set()
    for record in index['records']:
        if coverage['callSites'] >= MAX_CALLS or coverage['inspectedBodyNodes'] >= MAX_AST_NODES:
            _warn(coverage, 'Java field-call inspection budget reached.')
            break
        if record['kind'] not in METHODS:
            continue
        if not _source_current(record, index):
            _warn(coverage, 'Source changed between Java annotation and call capture; caller omitted.')
            continue
        calls, shadows = _calls(record, coverage)
        for call in calls:
            if coverage['callSites'] >= MAX_CALLS:
                _warn(coverage, 'Java field-call site budget reached.')
                break
            coverage['callSites'] += 1
            match = _target(record, call, shadows, index)
            if match is None:
                coverage['unresolvedCalls'] += 1
                continue
            target, field = match
            edge = _edge(record, call, target, field)
            key = edge['source'], edge['target'], edge['source_location']
            if key not in known_edges:
                edges.append(edge)
                known_edges.add(key)
                coverage['derivedCalls'] += 1
    facts['status'] = 'partial' if coverage['limited'] else 'complete'
    return {**graph, 'edges': edges, 'metadata': metadata}
