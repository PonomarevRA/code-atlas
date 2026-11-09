"""Contract checks for lazy original-AST pages and source-bound opaque references."""
import base64
from hashlib import sha256
import json

import pytest

from atlas.method_flow_chunks import build_method_flow_chunk, method_flow_chunk_availability
from atlas.source_viewer import SourceError


def graph_source(tmp_path, text, *, filename='test.ts', line=1, name='Run'):
    """Write one selected graph-owned declaration without inventing captured provenance."""
    (tmp_path / filename).write_text(text)
    return {'nodes': [{'id': 'method', 'file': filename, 'kind': 'method',
                       'label': name + '()', 'line': line}]}


def child_chunk(tmp_path, graph, page, kind):
    """Follow the returned reference of an existing immediate child."""
    child = next(item for item in page['children'] if item['type'] == kind)
    return build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot', child['ref'])


def forged(reference, **changes):
    """Recompute the public checksum to prove selectors are validated rather than trusted."""
    encoded = reference.split('.')[0]
    payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
    payload.update(changes)
    data = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip('=') + '.' + sha256(data).hexdigest()[:24]


@pytest.mark.parametrize('filename,text,line', [
    ('test.cs', 'class T {\nvoid Run(){ Call(); }\n}', 2),
    ('test.ts', 'const Run = async () => { Call(); };', 1),
    ('test.tsx', 'function Run(){ return <div/>; }', 1),
    ('test.js', 'function Run(){Call();}', 1),
    ('test.py', 'def Run():\n  Call()\n', 1),
    ('test.java', 'class T {\nvoid Run(){Call();}\n}', 2),
    ('test.go', 'package t\nfunc Run(){Call()}', 2),
    ('test.cpp', 'void Run(){Call();}', 1),
    ('test.kt', 'fun Run(){Call()}', 1),
    ('test.php', '<?php function Run(){Call();}', 1),
    ('test.rs', 'fn Run(){Call();}', 1),
    ('test.swift', 'func Run(){Call()}', 1),
])
def test_installed_language_grammars(tmp_path, filename, text, line):
    """All bundled language grammars expose syntax without importing project modules."""
    graph = graph_source(tmp_path, text, filename=filename, line=line)
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert page['available'] and page['children']
    assert page['analysisKind'] == 'syntax-outline' and page['flowStatus'] == 'not-computed'
    assert 'methodFlow' not in page and 'readabilityRecommendations' not in page


def test_oversized_body_has_bounded_pages_and_late_child(tmp_path):
    """A >20000-node body remains reachable without serializing or retaining the full tree."""
    text = 'function Run(){\n' + ''.join(f'Work{i}();\n' for i in range(4500)) + '}\n'
    graph = graph_source(tmp_path, text)
    root = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    body = child_chunk(tmp_path, graph, root, 'statement_block')
    assert body['childCount'] == 4500
    assert len(body['children']) == 40 and body['pageHasMore']
    assert body['hasMore'] and body['nextRef']
    offsets = [body['pageOffset']]
    while body['nextRef']:
        body = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot', body['nextRef'], limit=64)
        assert len(body['children']) <= 64
        offsets.append(body['pageOffset'])
    assert offsets == sorted(set(offsets)) and 'Work4499' in body['children'][-1]['label']
    assert not body['pageHasMore'] and body['nextRef'] is None
    assert body['limits']['methodFlowUnchanged'] == {'maxAstNodes': 20000, 'maxSteps': 180, 'maxDepth': 24}


def test_deep_callback_and_parent_links_are_syntax_only(tmp_path):
    """Request-local pages can cross a >24-deep subtree without claiming its callback executes."""
    text = 'function Run(){\n register(() => ' + 'Wrap(' * 30 + 'Deep()' + ')' * 30 + ');\n}'
    graph = graph_source(tmp_path, text)
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    for kind in ('statement_block', 'expression_statement', 'call_expression', 'arguments', 'arrow_function'):
        prior = page['currentRef']
        page = child_chunk(tmp_path, graph, page, kind)
        assert page['parentRef'] == prior
    assert page['current']['deferredBoundary']
    for _ in range(30):
        page = child_chunk(tmp_path, graph, page, 'call_expression')
        page = child_chunk(tmp_path, graph, page, 'arguments')
    page = child_chunk(tmp_path, graph, page, 'call_expression')
    assert page['current']['label'] == 'Deep()'
    leaf = child_chunk(tmp_path, graph, page, 'identifier')
    assert not leaf['children'] and not leaf['hasMore'] and leaf['nextRef'] is None
    assert leaf['flowStatus'] == 'not-computed'


def test_deterministic_refs_and_exact_utf8_coordinates(tmp_path):
    """Span offsets use original decoded UTF-8 bytes, including CRLF and multibyte characters."""
    text = 'function Run(){\r\n  const имя = "🌍";\r\n  Call(имя);\r\n}\r\n'
    graph = graph_source(tmp_path, text)
    first = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert first['currentRef'] == build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')['currentRef']
    body = child_chunk(tmp_path, graph, first, 'statement_block')
    call = body['children'][1]
    span = call['span']
    assert text.encode()[span['startByte']:span['endByte']].decode() == 'Call(имя);'
    assert span['startPoint'] == {'row': 2, 'column': 2}
    assert span['endPoint'] == {'row': 2, 'column': 15}


def test_changed_source_and_cross_node_snapshot_are_refused(tmp_path):
    """Ref replay cannot silently cross source revisions, snapshots or selected graph nodes."""
    graph = graph_source(tmp_path, 'function Run(){ Call(); }')
    reference = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')['currentRef']
    graph['nodes'].append({**graph['nodes'][0], 'id': 'other'})
    for node_id, snapshot in [('method', 'different'), ('other', 'snapshot')]:
        with pytest.raises(SourceError) as error:
            build_method_flow_chunk(tmp_path, graph, node_id, snapshot, reference)
        assert error.value.status == 409
    (tmp_path / 'test.ts').write_text('function Run(){ NewCall(); }')
    with pytest.raises(SourceError) as error:
        build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot', reference)
    assert error.value.status == 409


def test_tampered_selectors_cannot_leave_declaration(tmp_path):
    """Even a recomputed checksum cannot promote another method to the selected declaration."""
    text = 'function Run(){ First(); }\nfunction Other(){ Secret(); }'
    graph = graph_source(tmp_path, text)
    reference = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')['currentRef']
    other_start = text.index('function Other')
    invalid = [forged(reference, current=[other_start, len(text), 'function_declaration']),
               forged(reference, anchor=[other_start, len(text), 'function_declaration']),
               forged(reference, file='other.ts'), forged(reference, offset=100),
               forged(reference, current=[0, 3, 'not-a-node'])]
    for token in invalid:
        with pytest.raises(SourceError):
            build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot', token)


@pytest.mark.parametrize('reference', ['', 'junk', '.' * 5000, 'e30.bad', '!!!!.bad'])
def test_invalid_refs(tmp_path, reference):
    """Malformed transport cursors are clear request errors rather than uncaught parser failures."""
    graph = graph_source(tmp_path, 'function Run(){}')
    with pytest.raises(SourceError) as error:
        build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot', reference)
    assert error.value.status == 400


@pytest.mark.parametrize('limit', [0, 65, True, 1.5, '40'])
def test_page_limit_is_strict(tmp_path, limit):
    """The direct service enforces the same bounded page contract as the transport DTO."""
    graph = graph_source(tmp_path, 'function Run(){}')
    with pytest.raises(SourceError):
        build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot', limit=limit)


def test_ambiguous_or_stale_line_never_borrows_another_method(tmp_path):
    """Name plus declaration header line must identify exactly one original syntax node."""
    graph = graph_source(tmp_path, 'function Run(){} function Run(){}')
    result = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert not result['available'] and result['reason'] == 'declaration-missing-or-ambiguous'
    graph = graph_source(tmp_path, 'function Run(){\nCall();\n}', line=2)
    assert not build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')['available']


def test_captured_hash_change_and_unknown_provenance(tmp_path):
    """Changed captured source is refused; missing provenance remains explicitly unknown."""
    graph = graph_source(tmp_path, 'function Run(){}')
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert page['sourceProvenance']['status'] == 'unknown'
    graph['nodes'][0]['callableFacts'] = {'sourceKind': 'snapshot-captured-source', 'sourceHash': 'a' * 64}
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert not page['available'] and page['reason'] == 'snapshot-source-changed'


def test_safe_reader_and_discovery(tmp_path, monkeypatch):
    """Discovery does no reads; endpoint only reads graph-owned source inside the project root."""
    descriptor = method_flow_chunk_availability({'file': 'test.ts'})
    assert descriptor['supported'] and descriptor['requestRequired']
    assert descriptor['toolName'] == 'get_method_flow_chunk' and descriptor['status'] == 'not-probed'
    assert not method_flow_chunk_availability({'file': 'test.txt'})['supported']
    assert not method_flow_chunk_availability({'file': 'test.ts', 'kind': 'external'})['supported']
    graph = {'nodes': [{'id': 'method', 'file': '../secret.ts', 'kind': 'method', 'line': 1, 'label': 'Run()'}]}
    with pytest.raises(SourceError):
        build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    graph = graph_source(tmp_path, 'function Run(){}')
    monkeypatch.setattr('atlas.method_flow_chunks._parse', lambda *_: (None, 'parser-unavailable'))
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert not page['available'] and page['reason'] == 'parser-unavailable'


def test_local_parse_errors_are_visible(tmp_path):
    """Partial syntax can be inspected but is never advertised as fully parsed execution."""
    graph = graph_source(tmp_path, 'function Run(){\n const a = ;\n}')
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert page['available'] and page['hasParseError'] and page['status'] == 'partial'


def test_serialized_response_budget_keeps_atomic_child_cursor(tmp_path):
    """Large graph IDs shorten pages by bytes, retaining the first omitted child in nextRef."""
    graph = graph_source(tmp_path, 'function Run(){\n' + 'Work();\n' * 100 + '}')
    graph['nodes'][0]['id'] = 'm' * 900
    node_id = graph['nodes'][0]['id']
    root = build_method_flow_chunk(tmp_path, graph, node_id, 'snapshot')
    reference = next(child['ref'] for child in root['children'] if child['type'] == 'statement_block')
    page = build_method_flow_chunk(tmp_path, graph, node_id, 'snapshot', reference, limit=64)
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 64000
    assert 0 < len(page['children']) < 64 and page['nextRef']
    next_page = build_method_flow_chunk(tmp_path, graph, node_id, 'snapshot', page['nextRef'], limit=64)
    assert next_page['pageOffset'] == len(page['children'])
    assert next_page['children'][0]['span']['startByte'] == page['children'][-1]['span']['endByte'] + 1


@pytest.mark.parametrize('filename,text', [
    ('test.ts', 'const Run =\n async () => {\n Call();\n };'),
    ('test.py', '@decorator\ndef Run():\n Call()\n'),
])
def test_declaration_wrapper_can_precede_callable_line(tmp_path, filename, text):
    """Saved binding/decorator header lines resolve the wrapped original declaration exactly."""
    graph = graph_source(tmp_path, text, filename=filename)
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert page['available'] and page['current']['span']['startPoint']['row'] == 1


def test_captured_type_does_not_borrow_same_named_constructor(tmp_path):
    """A generic Graphify symbol carrying class facts cannot become its constructor."""
    graph = graph_source(tmp_path, 'class Run { Run(){} }', filename='test.cs')
    graph['nodes'][0].update(kind='symbol', memberFacts={'kind': 'class', 'name': 'Run'})
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert not page['available'] and page['reason'] == 'declaration-anchor-missing'


def test_large_minified_method_can_start_chunk_navigation(tmp_path):
    """One-line generated bodies cannot consume selection budget on unrelated call expressions."""
    graph = graph_source(tmp_path, 'function Run(){' + 'Work();' * 5000 + '}')
    root = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert root['available']
    body = child_chunk(tmp_path, graph, root, 'statement_block')
    assert body['childCount'] == 5000 and body['nextRef']
    assert body['limits']['maxSelectionAstNodes'] == 20000


@pytest.mark.parametrize('text', [
    'const Run=()=>{};' + 'Work();' * 5000 + 'const Run=()=>{};',
    'function Run(){' + 'Work();' * 5000 + 'const Run=()=>{};}',
])
def test_minified_selection_keeps_duplicate_binding_ambiguity(tmp_path, text):
    """Pruning by lexical name cannot conceal a later or nested same-line bound closure."""
    graph = graph_source(tmp_path, text)
    page = build_method_flow_chunk(tmp_path, graph, 'method', 'snapshot')
    assert not page['available'] and page['reason'] == 'declaration-missing-or-ambiguous'
