"""Bounded sequential incremental analysis, with explicit full refresh fallbacks."""

import json

from .hierarchy import aggregate, parent_map, repair_aggregate
from .model import (VERSION, ModuleUpdateError, UpdateResult, UpdateState, budget,
                    canonical_partition, capture_projection, encoded, fingerprint, full_modularity)

EPSILON = 1e-12


def _delta(before, after):
    """Describe every difference of observed inter-unit counts, not hidden source edges."""
    old = {(a, b): w for a, b, w in before.edges}
    new = {(a, b): w for a, b, w in after.edges}
    old_owners, new_owners = dict(before.ownership), dict(after.ownership)
    changes = []
    for source, target in sorted(set(old) | set(new)):
        previous, current = old.get((source, target), 0), new.get((source, target), 0)
        if previous != current:
            changes.append({'source': source, 'target': target, 'before': previous, 'after': current})
    return {'version': 'atlas-module-delta/v1', 'coverage': 'complete-observed-objective-graph-difference',
            'fromSnapshotId': before.snapshot_id, 'toSnapshotId': after.snapshot_id,
            'fromProjectionHash': before.projection_hash, 'toProjectionHash': after.projection_hash,
            'fromSourceGraphHash': before.source_graph_hash, 'toSourceGraphHash': after.source_graph_hash,
            'addedUnits': sorted(set(after.nodes) - set(before.nodes)),
            'removedUnits': sorted(set(before.nodes) - set(after.nodes)), 'edgeChanges': changes,
            'profileChanged': before.profile != after.profile,
            'ownershipChanged': any(old_owners[node] != new_owners[node] for node in set(old_owners) & set(new_owners)),
            'sourceCoverageChanged': before.coverage_json != after.coverage_json}


def derive_delta(old_projection, new_projection):
    """Derive a complete observed delta from two validated host-captured projections.

    Its completeness is limited to those projections. It does not turn partial
    graph coverage into full source knowledge or authenticate caller identities.
    """
    return _delta(capture_projection(old_projection), capture_projection(new_projection))


def _owners(partition):
    """Index a validated partition for one candidate search stage."""
    return {node: index for index, group in enumerate(partition) for node in group}


def _neighbors(graph):
    """Use either incident direction for candidates while retaining directed scoring."""
    result = {node: set() for node in graph.nodes}
    for source, target, _ in graph.edges:
        result[source].add(target)
        result[target].add(source)
    return result


def _expand(nodes, partition, neighbors):
    """Rescan touched communities and their neighbors; this may reach the whole graph."""
    result = set(nodes)
    for group in partition:
        if result.intersection(group):
            result.update(group)
    for node in tuple(result):
        result.update(neighbors[node])
    return result


class _CandidateBudget:
    """Count every candidate full-Q evaluation across ground/refinement/aggregate stages."""

    def __init__(self, graph, limit):
        self.graph, self.limit, self.used = graph, limit, 0
        self.stages = {'ground': 0, 'refinement': 0, 'aggregate': 0}

    @property
    def exhausted(self):
        """Stop before evaluating an uncounted candidate."""
        return self.used >= self.limit

    def evaluate(self, partition, stage):
        """Evaluate a complete candidate exactly once; callers must check the limit."""
        if self.exhausted:
            raise AssertionError('Uncounted candidate evaluation')
        self.used += 1
        self.stages[stage] += 1
        return full_modularity(self.graph, partition)


def _relocate(partition, block, target):
    """Move an indivisible block to one existing community or a new singleton block."""
    source = _owners(partition)[block[0]]
    groups = [set(group) for group in partition]
    groups[source].difference_update(block)
    if target is None:
        groups.append(set(block))
    else:
        groups[target].update(block)
    return tuple(sorted(tuple(sorted(group)) for group in groups if group))


def _move_blocks(graph, partition, blocks, active, counter, stage):
    """Accept only fully evaluated positive moves; deterministic order limits path ambiguity.

    Ground blocks are individual units; aggregate blocks are retained refined
    communities. Eight sweeps are a pilot bound, not convergence certification.
    """
    neighbors, visited, moves = _neighbors(graph), set(), 0
    current = full_modularity(graph, partition)
    active = set(active)
    for _ in range(8):
        changed, next_active = False, set()
        for block in blocks:
            if not active.intersection(block) or counter.exhausted:
                continue
            visited.update(block)
            owners = _owners(partition)
            source = owners[block[0]]
            candidates = {owners[peer] for node in block for peer in neighbors[node]} - {source}
            targets = sorted(candidates) + ([None] if len(partition[source]) > len(block) else [])
            best, best_score = partition, current
            for target in targets:
                if counter.exhausted:
                    break
                proposal = _relocate(partition, block, target)
                score = counter.evaluate(proposal, stage)
                if score > best_score + EPSILON:
                    best, best_score = proposal, score
            if best != partition:
                touched = set(partition[source])
                for group in best:
                    if set(block).intersection(group):
                        touched.update(group)
                partition, current = best, best_score
                next_active.update(_expand(touched, partition, neighbors))
                changed, moves = True, moves + 1
        if counter.exhausted or not changed:
            break
        active = next_active
    return partition, visited, moves


def _refine(graph, partition, active, counter, previous=None):
    """Keep unaffected refined groups and positively merge connected singleton pieces.

    Candidate merges stay inside one current logical parent. This deliberately
    simple deterministic refinement is an Atlas adaptation, not Leiden BestRef.
    """
    old = previous or ()
    refined, touched = [], set()
    for group in partition:
        members = set(group)
        retained = [part for part in old if set(part) <= members]
        covered = set()
        for part in retained:
            covered.update(part)
        if not members.intersection(active) and covered == members:
            refined.extend(retained)
        else:
            refined.extend((node,) for node in group)
            touched.update(group)
    refined = tuple(sorted(refined))
    logical = _owners(partition)
    while not counter.exhausted:
        owner = _owners(refined)
        pairs = set()
        for source, target, _ in graph.edges:
            a, b = owner[source], owner[target]
            if a != b and logical[source] == logical[target] and (source in touched or target in touched):
                pairs.add(tuple(sorted((a, b))))
        current = full_modularity(graph, refined)
        best, best_score = refined, current
        for a, b in sorted(pairs):
            if counter.exhausted:
                break
            proposal = tuple(sorted([part for i, part in enumerate(refined) if i not in {a, b}]
                                    + [tuple(sorted(refined[a] + refined[b]))]))
            score = counter.evaluate(proposal, 'refinement')
            if score > best_score + EPSILON:
                best, best_score = proposal, score
        if best == refined or best_score <= current + EPSILON:
            break
        refined = best
    return refined


def _adapt_previous(partition, nodes):
    """Drop removed units and add new singletons for measurement only, not constraints."""
    known, covered, result = set(nodes), set(), []
    for group in partition:
        common = tuple(node for node in group if node in known)
        if common:
            result.append(common)
            covered.update(common)
    result.extend((node,) for node in sorted(known - covered))
    return tuple(sorted(result))


def _membership(before, after):
    """Measure label-invariant pair disruption and explicit group overlaps on common IDs."""
    old, new = _owners(before), _owners(after)
    common = sorted(set(old) & set(new))
    changed, pairs = 0, len(common) * (len(common) - 1) // 2
    for i, node in enumerate(common):
        for peer in common[i + 1:]:
            changed += (old[node] == old[peer]) != (new[node] == new[peer])
    overlaps = {}
    for node in common:
        key = old[node], new[node]
        overlaps.setdefault(key, []).append(node)
    mapping = [{'beforeGroupHash': fingerprint(before[a]), 'afterGroupHash': fingerprint(after[b]),
                'sharedMembers': members} for (a, b), members in sorted(overlaps.items())]
    return {'commonUnits': len(common), 'coMembershipPairs': pairs, 'changedPairs': changed,
            'pairDisruption': changed / pairs if pairs else None, 'mapping': mapping,
            'addedUnits': sorted(set(new) - set(old)), 'removedUnits': sorted(set(old) - set(new)),
            'interpretation': 'Co-membership on common stable IDs; no semantic or geometric quality claim.'}


def _build(graph, starting, limit, previous=None, delta=None, fallback=None):
    """Run the bounded stages, repair retained aggregates and freeze one next result."""
    counter, neighbors = _CandidateBudget(graph, limit), _neighbors(graph)
    same_units = previous is not None and previous.graph.nodes == graph.nodes
    old_partition = previous.partition if previous else starting
    measured_previous = _adapt_previous(old_partition, graph.nodes)
    before_new = full_modularity(graph, measured_previous)
    full_frontier = previous is None or bool(fallback)
    changed_nodes = set()
    if delta:
        for change in delta['edgeChanges']:
            changed_nodes.update((change['source'], change['target']))
    global_change = previous is not None and previous.graph.total_weight != graph.total_weight
    if full_frontier or global_change:
        active = set(graph.nodes)
    else:
        active = _expand(changed_nodes, starting, neighbors)
    original_active = set(active)
    if graph.total_weight:
        partition, visited, ground_moves = _move_blocks(graph, starting, tuple((node,) for node in graph.nodes),
                                                       active, counter, 'ground')
        active.update(visited)
        refined = _refine(graph, partition, active, counter,
                          previous.refined if same_units and not fallback else None)
        partition, coarse_visited, aggregate_moves = _move_blocks(graph, partition, refined, active, counter, 'aggregate')
        visited.update(coarse_visited)
    else:
        partition = refined = canonical_partition(graph.nodes)
        visited, ground_moves, aggregate_moves = set(), 0, 0
    parents = parent_map(refined)
    if previous:
        aggregate_edges, repair = repair_aggregate(previous.graph.edges, graph.edges, dict(previous.parents),
                                                  parents, previous.aggregate_edges)
    else:
        aggregate_edges = aggregate(graph.edges, parents)
        repair = {'changedContributions': len(graph.edges), 'fullRebuildVerified': True,
                  'verificationScope': 'initial full aggregate build'}
    aggregate_partition = []
    for group in partition:
        aggregate_partition.append(tuple(sorted({parents[node] for node in group})))
    after = full_modularity(graph, partition)
    coverage = json.loads(graph.coverage_json)
    if not graph.total_weight:
        status = 'no-edges'
    elif coverage['status'] == 'complete':
        status = 'observed'
    else:
        status = 'partial'
    report = {'version': VERSION, 'algorithm': VERSION, 'paperReproduction': False,
              'snapshotId': graph.snapshot_id, 'sourceGraphHash': graph.source_graph_hash,
              'projectionHash': graph.projection_hash, 'profileHash': graph.profile,
              'previousSnapshotId': previous.snapshot_id if previous else None,
              'previousProjectionHash': previous.projection_hash if previous else None,
              'previousSourceGraphHash': previous.graph.source_graph_hash if previous else None,
              'groups': [list(group) for group in partition],
              'partitionHash': fingerprint({'version': VERSION, 'projectionHash': graph.projection_hash, 'groups': partition}),
              'coverage': coverage, 'status': status,
              'objective': {'kind': 'directed-count-inter-unit-gamma1/v1',
                            'beforeOldGraph': full_modularity(previous.graph, previous.partition) if previous else None,
                            'beforeNewGraph': before_new, 'afterNewGraph': after,
                            'oldWeight': previous.graph.total_weight if previous else None, 'newWeight': graph.total_weight,
                            'previousPartitionAdaptation': 'removed units dropped; new singleton units for measurement only'},
              'membership': _membership(old_partition, partition),
              'delta': {'status': 'full-refresh' if full_frontier else 'validated-observed',
                        'fallbackReasons': fallback or [], 'changedPairs': len(delta['edgeChanges']) if delta else None,
                        'globalWeightChanged': global_change, 'initialFrontierUnits': len(original_active),
                        'globalFrontier': len(original_active) == len(graph.nodes)},
              'search': {'evaluationLimit': limit, 'evaluationsUsed': counter.used, 'stages': counter.stages,
                         'stopReason': 'evaluation-budget' if counter.exhausted else 'bounded-stages-complete',
                         'visitedGroundUnits': len(visited), 'groundMoves': ground_moves, 'aggregateMoves': aggregate_moves,
                         'order': 'deterministic lexicographic; max8 sweeps/stage',
                         'improvementTolerance': EPSILON, 'optimal': False},
              'hierarchy': {'levels': 2, 'refinedGroups': len(refined), 'aggregateEdgePairs': len(aggregate_edges), **repair},
              'limitations': ['Atlas sequential adaptation; not LD-Leiden/Leiden or parallel decoupling.',
                              'Complete Q and aggregate verification may perform full graph work; no speedup/RSS guarantee.',
                              'Q is observed, not a bound under partial coverage; no monotonicity across snapshots.',
                              'Initial partition is a seed, not must-link/cannot-link constraints.',
                              'Candidate budget covers every stage; baseline/report Q and aggregate verification are outside it.']}
    return UpdateState(graph, partition, refined, tuple(sorted(parents.items())), aggregate_edges,
                       tuple(sorted(aggregate_partition)), encoded(report))


def initial_state(projection, partition=None, max_evaluations=4096):
    """Create immutable state from a captured projection and an optional partition seed.

    Args:
        projection: Host-captured module-analysis-v1 facts, identities and coverage.
        partition: Complete disjoint groups of unit IDs; None starts from singletons.
            This is a search seed, never must-link/cannot-link constraints.
        max_evaluations: Shared hard candidate-Q count, from1 through16384.
    Returns:
        Frozen UpdateState with two-level hierarchy and a detached JSON report.
    Raises:
        ModuleUpdateError: Projection/partition/budget is invalid or exceeds pilot limits.
    """
    limit = budget(max_evaluations)
    graph = capture_projection(projection)
    starting = canonical_partition(graph.nodes, partition)
    return _build(graph, starting, limit)


def update(previous_state, new_projection, delta=None, max_evaluations=4096):
    """Reuse host-retained state only for exact compatible observed deltas.

    Missing, malformed or inconsistent delta triggers complete fresh optimization.
    Node/ownership/profile/coverage changes use the same explicit fallback. The
    new projection itself must still be valid; invalid source facts are rejected.

    Args:
        previous_state: Trusted host-retained frozen UpdateState of this version.
        new_projection: Current host-captured module-analysis-v1 projection.
        delta: Exact derive_delta JSON; None or an inconsistent value requests full refresh.
        max_evaluations: Shared hard candidate-Q count, from1 through16384.
    Returns:
        UpdateResult with the next immutable state and detached source-bound report.
    Raises:
        ModuleUpdateError: State version, current projection or budget is invalid.
    """
    limit = budget(max_evaluations)
    if not isinstance(previous_state, UpdateState) or previous_state.version != VERSION:
        raise ModuleUpdateError('Expected host-retained UpdateState of current version')
    graph = capture_projection(new_projection)
    expected = _delta(previous_state.graph, graph)
    reasons = []
    if delta is None:
        reasons.append('delta-unknown')
    elif not isinstance(delta, dict):
        reasons.append('delta-invalid')
    else:
        try:
            if encoded(delta) != encoded(expected):
                reasons.append('delta-inconsistent-or-incomplete')
        except ModuleUpdateError:
            reasons.append('delta-invalid')
    for key, reason in (('addedUnits', 'unit-set-changed'), ('removedUnits', 'unit-set-changed'),
                        ('profileChanged', 'profile-changed'), ('ownershipChanged', 'ownership-changed'),
                        ('sourceCoverageChanged', 'source-coverage-changed')):
        if expected[key] and reason not in reasons:
            reasons.append(reason)
    starting = canonical_partition(graph.nodes) if reasons else previous_state.partition
    return UpdateResult(_build(graph, starting, limit, previous_state, expected, reasons))
