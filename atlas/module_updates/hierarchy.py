"""Exact two-level aggregate accounting, independent of optimization policy."""

from .model import fingerprint


def parent_map(refined):
    """Name refined groups from membership, separately from logical community labels."""
    result = {}
    for group in refined:
        parent = 'refined:' + fingerprint(group)
        for node in group:
            result[node] = parent
    return result


def aggregate(edges, parents):
    """Sum all directed contributions once, including aggregate internal self-loops."""
    weights = {}
    for source, target, weight in edges:
        key = parents[source], parents[target]
        weights[key] = weights.get(key, 0) + weight
    return tuple((a, b, weight) for (a, b), weight in sorted(weights.items()) if weight)


def repair_aggregate(old_edges, new_edges, old_parents, new_parents, old_aggregate):
    """Subtract old and add new incident summands exactly once per changed pair.

    Parent changes and weight changes may touch both ends of the same edge. The
    union of edge keys avoids double subtraction. A full rebuild is cross-checked
    explicitly for this small pilot; this is verification work, not a speed claim.
    """
    before = {(a, b): weight for a, b, weight in old_edges}
    after = {(a, b): weight for a, b, weight in new_edges}
    weights = {(a, b): weight for a, b, weight in old_aggregate}
    changed = []
    for source, target in sorted(set(before) | set(after)):
        pair = source, target
        old_pair = (old_parents.get(source), old_parents.get(target))
        new_pair = (new_parents.get(source), new_parents.get(target))
        if before.get(pair, 0) == after.get(pair, 0) and old_pair == new_pair:
            continue
        changed.append(pair)
        if pair in before:
            weights[old_pair] = weights.get(old_pair, 0) - before[pair]
        if pair in after:
            weights[new_pair] = weights.get(new_pair, 0) + after[pair]
    repaired = tuple((a, b, weight) for (a, b), weight in sorted(weights.items()) if weight)
    rebuilt = aggregate(new_edges, new_parents)
    if repaired != rebuilt:
        raise AssertionError('Aggregate subtract-add repair disagrees with full rebuild')
    return repaired, {'changedContributions': len(changed), 'fullRebuildVerified': True,
                      'verificationScope': 'complete observed aggregate graph; full rebuild work included'}
