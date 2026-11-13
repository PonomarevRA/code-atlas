"""Bounded call evidence for the changed functions in a saved review."""

CALL_RELATIONS = {'calls', 'dispatches_to'}
MAX_CAPTURED_EDGES = 16_000


def capture_call_edges(graph, functions):
    """Capture snapshot edges by function ordinal to keep the baseline compact."""
    positions = {row['nodeId']: index for index, row in enumerate(functions.values())}
    edges, seen = [], set()
    for edge in graph.get('edges', []):
        relation = edge.get('relation') or edge.get('type')
        if relation not in CALL_RELATIONS:
            continue
        source = positions.get(edge.get('source'))
        target = positions.get(edge.get('target'))
        if source is None or target is None:
            continue
        candidate = relation == 'dispatches_to' or str(edge.get('confidence', '')).upper() == 'INFERRED'
        row = (source, target, relation, candidate)
        if row in seen:
            continue
        if len(edges) == MAX_CAPTURED_EDGES:
            return {'edges': edges, 'truncated': True}
        seen.add(row)
        edges.append(row)
    return {'edges': edges, 'truncated': False}


def changed_call_edges(side, changes, name):
    """Project captured calls onto zero-based positions in the ordered change list."""
    captured = side.get('callEdges')
    if captured is None:
        return None
    changed = {row[name]['identity']: index for index, row in enumerate(changes) if row[name]}
    # JSON preserves the capture order of the function inventory in stored baselines.
    positions = [changed.get(identity) for identity in side['functions']]
    edges = []
    for source, target, relation, candidate in captured['edges']:
        left, right = positions[source], positions[target]
        if left is not None and right is not None:
            edges.append((left, right, relation, candidate))
    return {'edges': edges, 'truncated': captured['truncated']}
