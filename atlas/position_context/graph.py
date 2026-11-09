"""Two independent directed BFS frontiers sharing explicit output/scan budgets."""

from collections import deque
import hashlib
import json

from ..graph_context import _bounded_value
from .models import PositionContextError


def bounded_value(value, *, chars=512, items=24):
    """Reuse graph-context clipping while retaining complete retained identity/path values."""
    copied, limited = _bounded_value(value, chars=chars, items=items)
    def restore(original, result, key=''):
        """Restore identities only in records retained by the common bounded copier."""
        identity = key in {'id', 'source', 'target', 'file', 'source_file'} or key.endswith(('Id', 'Ids', 'Hash'))
        if isinstance(original, str):
            return original if identity else result
        if isinstance(original, list) and isinstance(result, list):
            return [restore(a,b,key) for a,b in zip(original,result)]
        if isinstance(original, dict) and isinstance(result, dict):
            for child in list(result):
                if child in original:
                    result[child] = restore(original[child],result[child],child)
        return result
    return restore(value,copied), limited


def size(value):
    """Measure actual UTF-8 JSON bytes with the transport's ordinary separators."""
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (TypeError, ValueError, RecursionError) as error:
        raise PositionContextError('Graph facts must be finite JSON') from error


def index(graph):
    """Reject malformed/ambiguous raw identity before any file access or traversal."""
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('edges'), list):
        raise PositionContextError('Graph requires node and edge arrays')
    if graph.get('metadata') is not None and not isinstance(graph['metadata'], dict):
        raise PositionContextError('Graph metadata must be an object')
    if len(graph['nodes']) > 100000 or len(graph['edges']) > 500000:
        raise PositionContextError('Position graph scan budget exceeded', 413, 'position_graph_budget')
    nodes = {}
    for node in graph['nodes']:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not node['id'] or node['id'] in nodes:
            raise PositionContextError('Graph raw IDs must be unique nonempty strings')
        nodes[node['id']] = node
        for key in ('memberFacts', 'callableFacts', 'declarationFacts'):
            if node.get(key) is not None and not isinstance(node[key], dict):
                raise PositionContextError('Malformed captured declaration facts')
    return nodes


def node_view(node):
    """Keep raw identity whole while bounding human labels and optional metadata."""
    value = {key: node[key] for key in ('id', 'label', 'kind', 'file', 'source_file', 'line', 'endLine') if key in node}
    copied, limited = bounded_value(value, chars=512, items=20)
    for key in ('file', 'source_file'):
        if isinstance(node.get(key), str):
            copied[key] = node[key]
    copied['metadataTruncated'] = limited
    return copied


def traverse(graph, nodes, anchor, body):
    """Never alternate edge directions along one path; cycles/diamonds retain minimum depths."""
    sections = {key: {'nodes': [], 'edges': [], 'depths': {}, 'truncated': False} for key in ('incoming', 'outgoing')}
    if anchor is None:
        return [], [], sections, {'scanLimitReached': False, 'missingEndpointEdges': 0}
    adjacency = {'incoming': {}, 'outgoing': {}}
    missing = 0
    for edge in graph['edges']:
        if not isinstance(edge, dict) or edge.get('relation') not in ('calls', 'dispatches_to'):
            continue
        a, b = edge.get('source'), edge.get('target')
        if not isinstance(a, str) or not isinstance(b, str) or a not in nodes or b not in nodes:
            missing += 1
            continue
        adjacency['outgoing'].setdefault(a, []).append(edge)
        adjacency['incoming'].setdefault(b, []).append(edge)
    directions = ('incoming', 'outgoing') if body.direction == 'both' else (body.direction,)
    queue = deque((direction, anchor, 0) for direction in directions)
    visited = {direction: {anchor: 0} for direction in directions}
    included, found, hashes, scans = {anchor: node_view(nodes[anchor])}, [], set(), 0
    while queue:
        direction, current, depth = queue.popleft()
        if depth >= body.depth:
            continue
        for edge in adjacency[direction].get(current, []):
            scans += 1
            if scans > 10000:
                sections[direction]['truncated'] = True
                break
            peer = edge['source'] if direction == 'incoming' else edge['target']
            if peer not in included and len(included) >= body.maxNodes:
                sections[direction]['truncated'] = True
                continue
            try:
                raw = json.dumps(edge, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
            except (TypeError, ValueError, RecursionError) as error:
                raise PositionContextError('Edge facts must be finite JSON') from error
            digest = hashlib.sha256(raw).hexdigest()
            if digest not in hashes and len(found) >= body.maxEdges:
                sections[direction]['truncated'] = True
                continue
            included.setdefault(peer, node_view(nodes[peer]))
            if digest not in hashes:
                copied, limited = bounded_value(edge, chars=512, items=30)
                # Optional field insertion order cannot erase the edge contract.
                # Hash still identifies the full original record, not this projection.
                for key in ('source', 'target', 'relation', 'id', 'file', 'source_file', 'sourceHash'):
                    if key in edge and isinstance(edge[key], (str, int, float, bool, type(None))):
                        copied[key] = edge[key]
                copied['edgeHash'] = digest
                copied['metadataTruncated'] = limited
                found.append(copied); hashes.add(digest)
            if digest not in sections[direction]['edges']:
                sections[direction]['edges'].append(digest)
            if peer not in visited[direction]:
                visited[direction][peer] = depth + 1
                sections[direction]['nodes'].append(peer)
                sections[direction]['depths'][peer] = depth + 1
                queue.append((direction, peer, depth + 1))
        if scans > 10000:
            for pending in directions:
                sections[pending]['truncated'] = True
            break
    return list(included.values()), found, sections, {'scanLimitReached': scans > 10000, 'missingEndpointEdges': missing}


def bound(result):
    """Fit whole records into 64000 bytes; mandatory identities are never sliced."""
    while size(result) > 64000:
        result['truncated'] = True
        evidence = result['sourceEvidence']
        if evidence.get('events'):
            evidence['events'].pop(); evidence['truncated'] = True
        elif evidence.get('literalNullArguments'):
            evidence['literalNullArguments'].pop(); evidence['truncated'] = True
        elif evidence.get('guards'):
            evidence['guards'].pop(); evidence['truncated'] = True
        elif evidence.get('callers'):
            evidence['callers'].pop(); evidence['callersTruncated'] = True; evidence['truncated'] = True
        elif result['edges']:
            edge = result['edges'].pop()
            for direction in ('incoming', 'outgoing'):
                section = result[direction]
                if edge['edgeHash'] in section['edges']:
                    section['edges'].remove(edge['edgeHash']); section['truncated'] = True
        elif len(result['nodes']) > 1:
            node = result['nodes'].pop()
            for direction in ('incoming', 'outgoing'):
                section = result[direction]
                section['nodes'] = [key for key in section['nodes'] if key != node['id']]
                section['depths'].pop(node['id'], None); section['truncated'] = True
        elif len(result['candidates']) > 1:
            result['candidates'].pop(); result['candidatesTruncated'] = True
        elif result.get('astCandidates'):
            result['astCandidates'].pop(); result['astCandidatesTruncated'] = True
        else:
            raise PositionContextError('Mandatory position identities exceed response budget', 413, 'position_response_budget')
    return result
