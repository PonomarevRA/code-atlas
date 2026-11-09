"""Indexed search must preserve bounded lexical facts and snapshot isolation, not merely return HTTP success."""
from itertools import islice
import random

from atlas.agent_views import search_nodes
from atlas.fact_search_index import build_fact_index


def legacy_match(node, needle):
    """Independent pre-index traversal oracle, including visit/depth/collection/string bounds."""
    fields = ('codeProjectFacts', 'testFacts', 'attributeFacts', 'memberFacts', 'entrypointFacts', 'htmlFacts')
    pending = [(node.get(key), 0) for key in fields if node.get(key)]
    remaining = 320
    while pending and remaining:
        value, depth = pending.pop()
        remaining -= 1
        if isinstance(value, str):
            if needle in value[:1000].casefold():
                return True
        elif depth < 6 and isinstance(value, dict):
            pending.extend((item, depth + 1) for key, item in islice(value.items(), 40)
                           if key not in {'rows', 'expression', 'sourceHash', 'modFingerprint', 'fingerprint', 'sourceKind'})
        elif depth < 6 and isinstance(value, list):
            pending.extend((item, depth + 1) for item in value[:20])
    return False


def test_index_matches_legacy_for_varied_queries_and_bounded_facts(monkeypatch):
    """Unicode, exclusions, collection boundaries and changing queries agree with an independent oracle."""
    rng = random.Random(307)
    values = ['Straße', 'Проверка', 'alpha', 'beta', 'x' * 1000 + 'hidden-tail']
    nodes = []
    for index in range(60):
        facts = {'tags': [rng.choice(values) for _ in range(25)], 'rows': ['excluded-body'],
                 'deep': {'a': {'b': {'c': {'d': {'e': {'f': 'too-deep'}}}}}}}
        nodes.append({'id': str(index), 'label': 'node-' + str(index), 'kind': 'method' if index % 2 else 'field',
                      'testFacts': facts})
    graph = {'nodes': nodes, 'edges': [], 'metadata': {}}
    facts = build_fact_index(graph)
    assert facts is not None
    monkeypatch.setattr('atlas.agent_views.matches_analysis_facts', legacy_match)
    for query in ['', 'STRASSE', 'пров', 'alpha', 'beta', 'hidden-tail', 'excluded-body', 'too-deep', 'missing', 'node-4']:
        for excluded in ([], ['FIELD']):
            expected = search_nodes(graph, query, 7, excluded)
            actual = search_nodes(graph, query, 7, excluded, load_fact_index=lambda: facts)
            assert actual == expected


def test_identical_rows_share_storage_and_oversize_falls_back():
    """Repeated annotations stay small; a refused index never becomes a partial search result."""
    node = {'id': 'one', 'label': 'one', 'testFacts': {'tags': ['shared'] * 20}}
    graph = {'nodes': [dict(node, id=str(index)) for index in range(1000)], 'edges': [], 'metadata': {}}
    facts = build_fact_index(graph)
    assert facts is not None and facts[0] is facts[-1]
    assert build_fact_index(graph, budget=64) is None
    assert search_nodes(graph, 'shared', 3, load_fact_index=lambda: None) == search_nodes(graph, 'shared', 3)


def test_broad_name_search_never_builds_fact_index():
    """A fast label-only query does not pay the cold index construction cost."""
    graph = {'nodes': [{'id': 'one', 'label': 'common'}, {'id': 'two', 'label': 'common'}], 'edges': [], 'metadata': {}}
    def unexpected():
        raise AssertionError('fact index should remain lazy')
    assert search_nodes(graph, 'common', load_fact_index=unexpected)['total'] == 2


def test_packed_rows_preserve_unicode_and_cannot_match_across_values():
    """UTF-8 boundaries, nulls, supplementary characters and lone surrogates match the legacy oracle."""
    node = {'id': 'one', 'testFacts': {'tags': ['ab', 'cd', 'Straße', 'Проверка', '🙂', '\ud800']}}
    graph = {'nodes': [node], 'edges': [], 'metadata': {}}
    row = build_fact_index(graph)[0]
    for value in ['ab', 'bc', 'abcd', 'ab\x00cd', 'abÿcd', 'STRASSE', 'ПРОВ', '🙂', '\ud800', 'absent']:
        needle = value.casefold()
        assert (needle.encode('utf-8', 'surrogatepass') in row) == legacy_match(node, needle)
