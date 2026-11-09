"""Bounded implementation summaries from snapshot dispatch edges, independent of path expansion."""
MAX_CATALOG_ENTRIES = 500
MAX_IMPLEMENTATIONS = 50


def implementation_catalog(nodes, adjacency, call_adjacency=None):
    """Keep counts for every dispatch target; materialize only a bounded global descriptor budget."""
    catalog, remaining = {}, MAX_CATALOG_ENTRIES
    call_adjacency = call_adjacency or adjacency
    for source, edges in adjacency.items():
        targets = sorted({e['target'] for e in edges if e.get('relation') == 'dispatches_to' and e['target'] in nodes})
        if not targets:
            continue
        entries = []
        for target in targets[:min(MAX_IMPLEMENTATIONS, remaining)]:
            node = nodes[target]
            file = str(node.get('file') or node.get('source_file') or '')[:700]
            facts = node.get('callableFacts') or {}
            declaring = str(facts.get('declaringType') or file.rsplit('/', 1)[-1].removesuffix('.cs'))[:300]
            name = str(node.get('label', '')).lstrip('.')[:300]
            owner = declaring.rsplit('.', 1)[-1] if '<' not in declaring else declaring
            test_source = any(part.lower() in {'test', 'tests'} for part in file.replace('\\', '/').split('/'))
            delegates = any(edge.get('relation') == 'calls' and edge.get('target') == source for edge in call_adjacency.get(target, []))
            entries.append({'id': target, 'label': f"{owner}.{name}", 'qualifiedLabel': f"{declaring}.{name}",
                            'scope': 'tests' if test_source else 'delegate' if delegates else 'source', 'file': file,
                            'line': node.get('line'), 'sourceKind': 'snapshot-candidate'})
        remaining -= len(entries)
        catalog[source] = {'total': len(targets), 'items': entries, 'truncated': len(entries) < len(targets),
                           'runtimeSelectionKnown': False}
    return catalog
