"""Exact test-container summaries for navigation; never infer owners from file names."""
from collections import defaultdict

from .test_entry_summary import build_test_entry_summary

MAX_PREVIEW_TESTS = 100


def capture_test_containers(nodes: list[dict], entries: list[dict], metadata: dict) -> None:
    """Attach a bounded class overview only to the parser's uniquely bound raw owner.

    The full captured catalogue remains the continuation for a large class.
    An unresolved owner stays unresolved rather than adopting a nearby class.
    """
    grouped = defaultdict(list)
    for entry in entries:
        if entry.get('containerNodeId'):
            grouped[entry['containerNodeId']].append(entry)
    for node in nodes:
        tests = grouped.get(node['id'])
        if not tests:
            continue
        node['testContainerFacts'] = {
            'version': 'test-container-v1', 'nodeId': node['id'],
            'declaredTestCount': len(tests), 'runtimeStatus': 'unknown',
            'sourceKind': 'snapshot-captured-source',
            'discoveryStatus': metadata.get('status', 'unknown'),
            'tests': [build_test_entry_summary(entry) for entry in tests[:MAX_PREVIEW_TESTS]],
            'truncated': len(tests) > MAX_PREVIEW_TESTS,
        }
