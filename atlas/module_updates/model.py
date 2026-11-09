"""Immutable, source-bound inputs for the bounded incremental analysis pilot."""

from dataclasses import dataclass
import hashlib
import json

VERSION = 'atlas-incremental-directed/v1'
MAX_UNITS = 200
MAX_EDGES = 5000
MAX_BYTES = 4 * 1024 * 1024
MAX_EVALUATIONS = 16384
DEPENDENCY_RELATIONS = frozenset({'calls', 'imports', 'references', 'inherits', 'implements', 'depends_on'})


class ModuleUpdateError(ValueError):
    """Expose a stable validation/budget error without HTTP dependencies."""

    def __init__(self, message, *, code='module_update_invalid', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def encoded(value):
    """Produce canonical finite JSON, preserving identity independently of ordering."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ModuleUpdateError('Expected bounded finite JSON') from error


def fingerprint(value):
    """Hash canonical facts; a hash is an identity, not proof of a caller's source."""
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def identifier(value, name, *, maximum=1024):
    """Validate one bounded printable identity without coercing other JSON types."""
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise ModuleUpdateError('Invalid ' + name)
    return value


def budget(value):
    """Validate the hard count of candidate objective evaluations, not elapsed time."""
    if type(value) is not int or not 1 <= value <= MAX_EVALUATIONS:
        raise ModuleUpdateError(f'max_evaluations must be 1..{MAX_EVALUATIONS}')
    return value


def canonical_partition(nodes, partition=None):
    """Validate complete disjoint ownership; missing units never acquire guessed owners."""
    if partition is None:
        return tuple((node,) for node in nodes)
    if not isinstance(partition, (list, tuple)) or len(partition) > MAX_UNITS:
        raise ModuleUpdateError('Expected a bounded partition array')
    known, seen, result = set(nodes), set(), []
    for group in partition:
        if not isinstance(group, (list, tuple)) or not group:
            raise ModuleUpdateError('Partition groups must be nonempty arrays')
        members = []
        for node in group:
            if not isinstance(node, str) or node not in known or node in seen:
                raise ModuleUpdateError('Partition contains unknown or duplicate units')
            seen.add(node)
            members.append(node)
        result.append(tuple(sorted(members)))
    if seen != known:
        raise ModuleUpdateError('Partition must cover every unit exactly once')
    return tuple(sorted(result))


@dataclass(frozen=True)
class CapturedGraph:
    """Retain immutable observed counts and the host-captured projection identities."""

    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str, int], ...]
    snapshot_id: str
    projection_hash: str
    source_graph_hash: str
    profile: str
    ownership: tuple[tuple[str, str], ...]
    coverage_json: str

    @property
    def total_weight(self):
        """Count directed inter-unit weight; unit self dependencies are excluded."""
        return sum(weight for _, _, weight in self.edges)


def capture_projection(projection):
    """Validate a module-analysis projection before constructing adjacency/state.

    This pure boundary checks consistency, not source authenticity. The host must
    obtain the projection from its pinned store, never from request JSON.
    """
    if not isinstance(projection, dict) or projection.get('version') != 'module-analysis-v1':
        raise ModuleUpdateError('Expected module-analysis-v1 projection')
    units, edges = projection.get('units'), projection.get('edges')
    if not isinstance(units, list) or not isinstance(edges, list):
        raise ModuleUpdateError('Projection requires units and edges arrays')
    if len(units) > MAX_UNITS or len(edges) > MAX_EDGES:
        raise ModuleUpdateError('Incremental pilot is limited to 200 units / 5000 edge records',
                                code='module_update_budget', status=413)
    raw = encoded(projection)
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise ModuleUpdateError('Projection exceeds 4 MiB pilot budget', code='module_update_budget', status=413)
    supplied_hash = identifier(projection.get('projectionHash'), 'projectionHash', maximum=64)
    facts = {key: value for key, value in projection.items() if key != 'projectionHash'}
    if supplied_hash != fingerprint(facts):
        raise ModuleUpdateError('Projection hash does not match captured facts')
    snapshot = identifier(projection.get('snapshotId'), 'snapshotId', maximum=128)
    source_hash = identifier(projection.get('sourceGraphHash'), 'sourceGraphHash', maximum=64)
    if len(source_hash) != 64 or any(c not in '0123456789abcdef' for c in source_hash):
        raise ModuleUpdateError('Expected a captured SHA256 sourceGraphHash')
    level = projection.get('level')
    if not isinstance(level, str) or level not in {'class', 'folder'} or not isinstance(projection.get('scope'), dict):
        raise ModuleUpdateError('Projection requires explicit class/folder scope')
    coverage = projection.get('coverage')
    if (not isinstance(coverage, dict) or not isinstance(coverage.get('status'), str)
            or coverage['status'] not in {'complete', 'partial', 'unknown'}):
        raise ModuleUpdateError('Projection requires explicit coverage status')
    nodes, ownership = [], []
    for unit in units:
        if not isinstance(unit, dict):
            raise ModuleUpdateError('Invalid unit record')
        if unit.get('kind') != level:
            raise ModuleUpdateError('Unit kind must match the declared class/folder level')
        node = identifier(unit.get('id'), 'unit ID')
        source_ids = unit.get('sourceIds')
        if not isinstance(source_ids, list) or not source_ids or len(source_ids) > 10000:
            raise ModuleUpdateError('Unit requires bounded captured sourceIds')
        for source in source_ids:
            identifier(source, 'raw source ID')
        if len(set(source_ids)) != len(source_ids):
            raise ModuleUpdateError('Duplicate raw source ID within unit')
        # A stable unit ID alone cannot hide changed raw ownership.
        signature = {key: value for key, value in unit.items() if key not in {'label', 'files', 'sourceIds'}}
        signature['sourceIds'] = sorted(source_ids)
        ownership.append((node, fingerprint(signature)))
        nodes.append(node)
    if len(set(nodes)) != len(nodes):
        raise ModuleUpdateError('Duplicate unit ID')
    raw_seen = set()
    for unit in units:
        for raw_id in unit['sourceIds']:
            if raw_id in raw_seen:
                raise ModuleUpdateError('Raw ownership overlaps across units')
            raw_seen.add(raw_id)
    known, counts = set(nodes), {}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ModuleUpdateError('Invalid edge record')
        source, target, weight = edge.get('source'), edge.get('target'), edge.get('count')
        relation = edge.get('relation')
        if not isinstance(relation, str) or relation not in DEPENDENCY_RELATIONS:
            raise ModuleUpdateError('Only versioned dependency relations belong in the module objective')
        if not isinstance(source, str) or not isinstance(target, str) or source not in known or target not in known:
            raise ModuleUpdateError('Edge references an unknown unit')
        if type(weight) is not int or not 0 < weight <= 10**9:
            raise ModuleUpdateError('Count weights must be positive bounded integers')
        if source != target:
            counts[source, target] = counts.get((source, target), 0) + weight
    profile = fingerprint({'version': projection['version'], 'level': level, 'scope': projection['scope'],
                           'objective': 'directed-count-inter-unit-gamma1/v1'})
    return CapturedGraph(tuple(sorted(nodes)), tuple((s, t, w) for (s, t), w in sorted(counts.items())),
                         snapshot, supplied_hash, source_hash, profile, tuple(sorted(ownership)), encoded(coverage))


def full_modularity(graph, partition):
    """Compute complete directed Q with all removal constants; zero weight is undefined.

    This objective uses every observed inter-unit edge and gamma=1. Partial source
    coverage makes it an observed value, never a lower or upper quality bound.
    """
    total = graph.total_weight
    if not total:
        return None
    owners = {node: index for index, group in enumerate(partition) for node in group}
    incoming, outgoing, internal = [0] * len(partition), [0] * len(partition), [0] * len(partition)
    for source, target, weight in graph.edges:
        a, b = owners[source], owners[target]
        outgoing[a] += weight
        incoming[b] += weight
        if a == b:
            internal[a] += weight
    return sum(internal[i] / total - outgoing[i] * incoming[i] / (total * total)
               for i in range(len(partition)))


@dataclass(frozen=True)
class UpdateState:
    """Own one immutable graph, logical partition and distinct refined hierarchy."""

    graph: CapturedGraph
    partition: tuple[tuple[str, ...], ...]
    refined: tuple[tuple[str, ...], ...]
    parents: tuple[tuple[str, str], ...]
    aggregate_edges: tuple[tuple[str, str, int], ...]
    aggregate_partition: tuple[tuple[str, ...], ...]
    report_json: str
    version: str = VERSION

    @property
    def report(self):
        """Return an independent JSON report; callers cannot mutate retained evidence."""
        return json.loads(self.report_json)

    @property
    def projection_hash(self):
        """Expose the exact retained projection identity for host snapshot checks."""
        return self.graph.projection_hash

    @property
    def snapshot_id(self):
        """Expose the retained snapshot identity without a live store lookup."""
        return self.graph.snapshot_id

    def to_dict(self):
        """Serialize bounded pilot state without source bodies or mutable retained objects."""
        return {'version': self.version, 'snapshotId': self.snapshot_id, 'projectionHash': self.projection_hash,
                'sourceGraphHash': self.graph.source_graph_hash, 'profileHash': self.graph.profile,
                'partition': [list(group) for group in self.partition],
                'refined': [list(group) for group in self.refined], 'parents': dict(self.parents),
                'aggregateEdges': [{'source': a, 'target': b, 'weight': w} for a, b, w in self.aggregate_edges],
                'aggregatePartition': [list(group) for group in self.aggregate_partition], 'report': self.report}


@dataclass(frozen=True)
class UpdateResult:
    """Return the next retained state and a detached read-only analysis report."""

    state: UpdateState

    @property
    def report(self):
        """Project the same immutable result as independent JSON on each access."""
        return self.state.report
