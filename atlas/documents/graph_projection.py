"""Ограниченная проекция containment/references без имитации графа вызовов."""

import json
from pathlib import Path

from .models import MAX_QUERY_BYTES, encode, require
from .queries import bound_id, database, project


def graph(root, manifest, *, document_id=None, max_nodes=120, max_edges=300, **unused):
    """Выбирает структурные узлы, затем только рёбра между выбранными узлами."""
    require(not unused, 'INVALID_QUERY', 'Неизвестные параметры графа: ' + ', '.join(unused))
    require(type(max_nodes) is int and 1 <= max_nodes <= 120 and type(max_edges) is int and 1 <= max_edges <= 300, 'GRAPH_LIMIT', 'Подграф ограничен 120 узлами и 300 рёбрами.')
    nodes, edges, ids, truncated = [], [], set(), False
    for binding in manifest['documents']:
        if document_id and binding['documentId'] != document_id:
            continue
        with database(Path(root) / 'revisions' / binding['revisionId'] / 'index.sqlite') as connection:
            for row in connection.execute("SELECT payload FROM nodes WHERE kind NOT IN ('row','cell') ORDER BY position"):
                node = project(json.loads(row[0]), binding)
                if len(nodes) >= max_nodes or len(encode({'nodes': nodes + [node], 'edges': edges}).encode()) > MAX_QUERY_BYTES - 2000:
                    truncated = True
                    break
                nodes.append(node)
                ids.add(node['id'])
            for node in nodes:
                if node.get('parentId') in ids and len(edges) < max_edges:
                    candidate = {'source': node['parentId'], 'target': node['id'], 'relation': 'contains', 'certainty': 'exact'}
                    if candidate not in edges:
                        if len(encode({'nodes': nodes, 'edges': edges + [candidate]}).encode()) > MAX_QUERY_BYTES - 2000:
                            truncated = True
                            break
                        edges.append(candidate)
            for row in connection.execute('SELECT payload FROM edges'):
                edge = json.loads(row[0])
                edge['source'] = bound_id(edge['source'], binding)
                edge['target'] = bound_id(edge['target'], binding)
                if edge['source'] in ids and edge['target'] in ids:
                    if len(edges) >= max_edges or len(encode({'nodes': nodes, 'edges': edges + [edge]}).encode()) > MAX_QUERY_BYTES - 1000:
                        truncated = True
                        break
                    edges.append(edge)
    return {'snapshotId': manifest['snapshotId'], 'nodes': nodes, 'edges': edges, 'truncated': truncated}
