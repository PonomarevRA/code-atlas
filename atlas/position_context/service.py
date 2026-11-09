"""Orchestrate safe position resolution, observed dependencies and bounded source facts."""

from .graph import bound, index, traverse, bounded_value
from .models import VERSION, PositionContextError, PositionContextInput
from .source import resolve
from .evidence import callers


def build(project_path, graph, body):
    """Build detached position evidence from host facts; never execute or mutate source."""
    if not isinstance(body, PositionContextInput):
        raise PositionContextError('Expected validated PositionContextInput')
    nodes = index(graph)
    for snapshot in (graph.get('snapshotId'), (graph.get('metadata') or {}).get('snapshotId')):
        if snapshot is not None and snapshot != body.snapshotId:
            raise PositionContextError('Graph snapshot differs from request', 409, 'position_snapshot_changed')
    selection = resolve(project_path, graph, body, nodes)
    analysis, descriptions = selection.pop('_analysis'), selection.pop('_descriptions')
    caller_evidence = callers(graph, nodes, selection['anchorId'], analysis, descriptions, body.direction)
    selected_nodes, edges, sections, coverage = traverse(graph, nodes, selection['anchorId'], body)
    description = selection.pop('sourceEvidence')
    evidence, limited = bounded_value({'events': description.get('events', []),
                                       'guards': (description.get('controlFlow') or {}).get('branches', []),
                                       'unresolved': description.get('unresolved', []),
                                       'unsupported': (description.get('controlFlow') or {}).get('unsupported', []),
                                       'controlFlowTruncated': (description.get('controlFlow') or {}).get('truncated', False),
                                       'literalNullArguments': description.get('literalNullArguments', []),
                                       'literalNullStatus': description.get('literalNullStatus', 'unavailable'),
                                       'literalNullArgumentsTruncated': description.get('literalNullArgumentsTruncated', False),
                                       **caller_evidence,
                                       'sourceStatus': description.get('sourceStatus', 'unavailable')},
                                      chars=512, items=24)
    evidence['truncated'] = (limited or any(selection['analysisTruncation'].values()) or caller_evidence['callersTruncated']
                             or evidence['literalNullArgumentsTruncated']
                             or evidence['controlFlowTruncated']
                             or any(caller.get('truncated', False) for caller in caller_evidence['callers']))
    evidence['coordinateSystem'] = 'one-based lines and UTF-8 byte columns; not IDE UTF-16 offsets'
    evidence['interpretation'] = 'Parsed lexical call/argument/guard candidates; argument indices are source order, not formal parameter binding. Not runtime binding, nullability or duplicate-check proof.'
    result = {'version': VERSION, 'snapshotId': body.snapshotId, 'position': {'file': selection['file'], 'line': body.line},
              **selection, **sections, 'nodes': selected_nodes, 'edges': edges, 'sourceEvidence': evidence,
              'budgets': {'depth': body.depth, 'direction': body.direction, 'maxNodes': body.maxNodes, 'maxEdges': body.maxEdges,
                          'responseBytes': 64000, 'sourceFiles': 5, 'sourceBytes': 1024 * 1024, 'descriptions': 120,
                          'scannedAdjacencyEdges': 10000, 'sharedOutputBudgetDirectionOrder': ['incoming', 'outgoing']},
              'coverage': {'graphAnalysisStatus': (graph.get('metadata') or {}).get('analysisStatus', 'unknown'), **coverage},
              'candidatesTruncated': selection['descriptionLimitReached'],
              'truncated': evidence['truncated'] or selection['descriptionLimitReached'] or any(selection['analysisTruncation'].values())
                           or any(section['truncated'] for section in sections.values()),
              'interpretation': 'Anchor mapping is exact only when status=resolved. Unknown/changed freshness retains snapshot candidates; absence of graph edges is not proof of no dependency.'}
    return bound(result)
