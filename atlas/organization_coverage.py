"""Independent evidence completeness for selected-scope organization measurements."""

DIMENSIONS = ('inventory', 'local', 'outgoing', 'incoming', 'cycles', 'fragmentation')


def coverage_fact(complete: bool, reason: str, scope: str) -> dict:
    """Describe evidence completeness without replacing unknown measurements with a score."""
    return {'status': 'complete' if complete else 'partial',
            'reason': 'captured-complete-facts' if complete else reason, 'scope': scope}


def file_coverage(filename: str, *, inventory: bool, local: bool, outgoing: bool,
                  topology: bool, source_hash=None) -> dict:
    """Keep local facts independent; unlocalized targets limit incoming/transitive facts."""
    result = {
        'inventory': coverage_fact(inventory, 'incomplete-declaration-inventory', filename),
        'local': coverage_fact(local, 'incomplete-local-metrics', filename),
        'outgoing': coverage_fact(outgoing, 'unresolved-source-dependencies', filename),
        'incoming': coverage_fact(topology, 'unlocalized-target-or-incomplete-scope', 'selected-source-graph'),
        'cycles': coverage_fact(topology, 'absence-not-proven-on-partial-graph', 'selected-source-graph'),
        'fragmentation': coverage_fact(topology and local, 'partial-topology-or-local-inventory', 'selected-source-graph'),
    }
    if source_hash:
        for fact in result.values():
            fact['sourceHash'] = source_hash
    return result


def folder_coverage(path: str, members: list[dict]) -> dict:
    """Aggregate per-dimension completeness without allowing a sibling call to hide local size."""
    result = {}
    for dimension in DIMENSIONS:
        complete = all(row[dimension]['status'] == 'complete' for row in members)
        result[dimension] = coverage_fact(complete, 'incomplete-folder-' + dimension, path)
    return result


def coverage_summary(rows: list[dict]) -> dict:
    """Expose a denominator for every dimension; legacy rows provide no new coverage evidence."""
    result = {}
    for dimension in DIMENSIONS:
        known = sum(row.get('coverage', {}).get(dimension, {}).get('status') == 'complete' for row in rows)
        result[dimension] = {'complete': known, 'unknown': len(rows) - known, 'total': len(rows)}
    return result
