"""Continuation preserves all graph occurrences and exposes exact nested description details."""

from copy import deepcopy
import json

import pytest

from atlas.graph_context import read_graph_neighbors, GraphContextError
from atlas.analysis_sections import section_page, MAX_PAGE_BYTES
from atlas.analysis_contexts import AnalysisContextError
from atlas.continuation import encode_reference, decode_reference


def neighborhood(count):
    """Build a high-degree node with parallel edges and a self-loop among ordinary calls."""
    return {'metadata': {'snapshotId': 's'},
            'nodes': [{'id': 'root', 'label': 'Корень'}] + [{'id': str(i), 'label': 'Узел'} for i in range(count)],
            'edges': [{'source': 'root', 'target': str(i), 'relation': 'calls', 'line': i} for i in range(count)]
                     + [{'source': 'root', 'target': '0', 'relation': 'calls', 'line': 0},
                        {'source': 'root', 'target': 'root', 'relation': 'calls'}]}


def test_thousand_edges_duplicate_and_loop_are_reachable_exactly_once():
    """Each edge occurrence crosses exactly one page, with no endpoint-based deduplication."""
    graph = neighborhood(1000)
    cursor, seen = None, []
    while True:
        page = read_graph_neighbors(graph, 'root', 'outgoing', 120, cursor)
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 64000
        assert page['pageOffset'] == len(seen)
        seen.extend(page['edges'])
        cursor = page['nextRef']
        if cursor is None:
            assert not page['pageHasMore']
            break
    assert seen == graph['edges']


@pytest.mark.parametrize('change', ['node', 'direction', 'snapshot', 'edges'])
def test_continuation_rejects_changed_scope_or_edge_revision(change):
    """Reusing an offset cannot silently skip or duplicate facts after scope changes."""
    graph = neighborhood(10)
    reference = read_graph_neighbors(graph, 'root', 'outgoing', 2)['nextRef']
    node, direction = 'root', 'outgoing'
    if change == 'node':
        node = '0'
    elif change == 'direction':
        direction = 'incoming'
    elif change == 'snapshot':
        graph['metadata']['snapshotId'] = 'new'
    else:
        graph['edges'].append(deepcopy(graph['edges'][0]))
    with pytest.raises(GraphContextError) as error:
        read_graph_neighbors(graph, node, direction, 2, reference)
    assert error.value.status == 409


@pytest.mark.parametrize('reference', ['', 'bad', 'e30.wrong', encode_reference({'scope': {}, 'offset': True})])
def test_invalid_neighbor_selector_is_a_controlled_error(reference):
    """Malformed and forged selector shapes fail before accepting an offset."""
    with pytest.raises(GraphContextError) as error:
        read_graph_neighbors(neighborhood(2), 'root', cursor=reference)
    assert error.value.status == 400


def test_byte_limited_neighbor_pages_still_advance():
    """Large identity strings limit the page by bytes rather than losing later edges."""
    graph = neighborhood(20)
    replacements = {str(i): str(i) + 'x' * 2500 for i in range(20)}
    for node in graph['nodes']:
        node['id'] = replacements.get(node['id'], node['id'])
    for edge in graph['edges']:
        edge['target'] = replacements.get(edge['target'], edge['target'])
    first = read_graph_neighbors(graph, 'root', 'outgoing', 120)
    assert first['pageHasMore'] and 0 < len(first['edges']) < 22
    second = read_graph_neighbors(graph, 'root', 'outgoing', 120, first['nextRef'])
    assert second['pageOffset'] == len(first['edges'])
    assert first['edges'] + second['edges'] == graph['edges']


def test_section_tree_roundtrip_preserves_deep_flow_unicode_and_large_text():
    """References reveal exact nested values rather than reproducing compact-description omissions."""
    scope = {'contextId': 'c', 'projectId': 'p', 'snapshotId': 's', 'nodeId': 'n',
             'section': 'methodFlow', 'sourceHash': 'a' * 64}
    source = {'steps': [{'kind': 'call', 'label': 'Вызов ' + str(i), 'arguments': ['ю' * 3000]}
                        for i in range(100)], 'truncated': True, 'unsupported': ['dynamic']}

    def reconstruct(reference=None):
        """Walk only server-returned child/page references, as an MCP agent would."""
        items = []
        while True:
            page = section_page(source, scope, reference, limit=8)
            assert len(json.dumps(page, ensure_ascii=False).encode()) <= MAX_PAGE_BYTES
            items.extend(page['items'])
            reference = page['nextRef']
            if reference is None:
                break
        values = {}
        for item in items:
            values[item['key']] = reconstruct(item['ref']) if item['hasChildren'] else item['value']
        if items and all(type(item['key']) is int for item in items):
            if all(isinstance(value, str) for value in values.values()) and list(values) != list(range(len(values))):
                return ''.join(values.values())
            return list(values.values())
        return values

    assert reconstruct() == source
    first = section_page(source, scope)
    with pytest.raises(AnalysisContextError) as error:
        section_page(source, {**scope, 'sourceHash': 'b' * 64}, first['items'][0]['ref'])
    assert error.value.status == 409
