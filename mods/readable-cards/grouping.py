"""Example custom algorithm using only the stable weighted-node input contract."""


def group(graph):
    """Keep all existing IDs, grouping methods separately from other node kinds."""
    methods, others = [], []
    for node in graph['nodes']:
        (methods if node.get('kind') in {'method','function'} else others).append(node['id'])
    return [members for members in [methods, others] if members]
