"""Find reviewable extraction candidates from observed calls inside substantial files."""

import networkx as nx


MIN_FILE_STATEMENTS = 80
MIN_GROUP_STATEMENTS = 12
MIN_GROUP_DECLARATIONS = 3
MAX_GROUPS = 3
MAX_EXAMPLES = 4
MAX_CANDIDATE_FILES = 3
MAX_PARTITION_FILES = 12
MAX_PARTITION_DECLARATIONS = 120
MAX_PARTITION_EDGES = 600


def decomposition_candidates(records, edges, by_id, nodes, confirmed_call):
    """Index only confirmed intra-file connections for files large enough to review."""
    graphs, canonical, sizes = {}, {}, {}
    for filename, items in records.items():
        statements = sum(node['complexity'].get('statementCount') or 0 for node in items)
        if statements < MIN_FILE_STATEMENTS:
            continue
        if len(items) < 2 * MIN_GROUP_DECLARATIONS:
            continue
        graph = nx.Graph()
        for node in items:
            graph.add_node(node['id'], declaration=node)
            canonical[filename, node.get('healthIdentity') or node['id']] = node['id']
        graphs[filename] = graph
        sizes[filename] = statements
    if not graphs:
        return {}
    for edge in edges:
        source = edge.get('source')
        target = edge.get('target')
        filename = by_id.get(source)
        if filename not in graphs or by_id.get(target) != filename:
            continue
        left, right = nodes[source], nodes[target]
        if not confirmed_call(edge, left):
            continue
        first = canonical.get((filename, left.get('healthIdentity') or source))
        second = canonical.get((filename, right.get('healthIdentity') or target))
        if first is not None and second is not None and first != second:
            graphs[filename].add_edge(first, second)
    eligible = [name for name, graph in graphs.items()
                if len(graph) <= MAX_PARTITION_DECLARATIONS and graph.number_of_edges() <= MAX_PARTITION_EDGES]
    partition_files = sorted(eligible, key=lambda name: (-sizes[name], name))[:MAX_PARTITION_FILES]
    for filename in partition_files:
        graphs[filename].graph['allowPartition'] = True
    return graphs


def _group(graph, members, *, partitioned=False):
    """Retain substantial groups and expose the observed boundary cost."""
    if len(members) < MIN_GROUP_DECLARATIONS:
        return None
    nodes = [graph.nodes[key]['declaration'] for key in members]
    statements = sum(node['complexity']['statementCount'] for node in nodes)
    if statements < MIN_GROUP_STATEMENTS:
        return None
    internal = graph.subgraph(members).number_of_edges()
    crossing = nx.cut_size(graph, members) if partitioned else 0
    # A connected linear flow should not be split just because a graph
    # partitioner can find a low-cut boundary on a chain.
    if partitioned and (internal < len(nodes) or internal < 2 * crossing):
        return None
    nodes.sort(key=lambda node: (str(node.get('label', '')).startswith('<'), node.get('line') or 0, node['id']))
    examples = [{'label': str(node.get('label') or node['id'])[:96], 'line': node.get('line')}
                for node in nodes[:MAX_EXAMPLES]]
    return statements, nodes[0]['id'], {'declarations': len(nodes), 'statements': statements,
            'internalPairs': internal, 'crossingPairs': crossing, 'examples': examples,
            'examplesTruncated': len(nodes) > MAX_EXAMPLES}


def _partition_groups(graph, components):
    """Look for bounded, dense pockets in connected files with explicit cross-group calls."""
    if not graph.graph.get('allowPartition'):
        return []
    result = []
    for component in components:
        if len(component) < 2 * MIN_GROUP_DECLARATIONS:
            continue
        subgraph = graph.subgraph(component)
        groups = [_group(graph, members, partitioned=True)
                  for members in nx.community.greedy_modularity_communities(subgraph)]
        groups = [group for group in groups if group is not None]
        if len(groups) >= 2:
            result.extend(groups)
    return result


def review_decomposition(graph, coverage):
    """Describe substantial observed groups, leaving the responsibility decision to review."""
    if graph is None or coverage['local']['status'] != 'complete':
        return None
    components = list(nx.connected_components(graph))
    groups = [group for members in components if (group := _group(graph, members)) is not None]
    kind = 'observed-call-groups'
    if len(groups) < 2:
        groups = _partition_groups(graph, components)
        kind = 'low-crossing-call-groups'
    if len(groups) < 2:
        return None
    groups.sort(key=lambda item: (-item[0], item[1]))
    return {'kind': kind, 'status': 'review',
            'callCoverage': coverage['outgoing']['status'],
            'groupCount': len(groups), 'groups': [group for _, _, group in groups[:MAX_GROUPS]],
            'groupsTruncated': len(groups) > MAX_GROUPS}


def summarize_decomposition(profiles):
    """Return three file candidates ordered by the amount of observed logic."""
    candidates = [row for row in profiles if row.get('decompositionReview')]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row['metrics']['statements'], row['file']))
    return {'status': 'review', 'candidateFileCount': len(candidates),
            'files': [{'file': row['file'], 'folder': row['folder'], **row['decompositionReview']}
                      for row in candidates[:MAX_CANDIDATE_FILES]],
            'truncated': len(candidates) > MAX_CANDIDATE_FILES}
