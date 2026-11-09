"""Bounded semantic sidecars preserve immutable Graphify snapshots and static uncertainty."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from itertools import islice
import json
from pathlib import Path
from uuid import uuid4

from .health_snapshot_overlay import file_stamp, snapshot_hash
from .mod_analysis import active_semantic_rules
from .semantic_models import SemanticCoverage, SemanticSymbol, SourceSpan
from .semantic_sources import DeclarationIndex
from .snapshot_objects import snapshot_source_stamp

VERSION = 'atlas-semantic-overlay-v1'
MAX_OVERLAY_BYTES = 8 * 1024 * 1024
MAX_FACTS = 1024


def overlay_path(snapshot):
    """Keep optional topology separate from health-only derivatives and raw vendor files."""
    return snapshot.with_name('semantic-overlay.json')


def semantic_stamp(snapshot, mods):
    """Stamp bounded package inputs without engine probes, hashing or source scans."""
    stamp = file_stamp(overlay_path(snapshot))
    if not stamp[1]:
        return stamp
    paths = [mods.state_path]
    for directory in (mods.bundled, mods.directory):
        paths.extend(sorted(islice(directory.glob('*/mod.json'), 64)))
    # Explicit activation pins all package assets. Their changes must revoke an
    # overlay even when the semantic declarations themselves are unchanged.
    with mods.lock:
        records = {root: value[1]['manifest'] for root, value in mods.cache.items()}
        relevant = {manifest['id'] for manifest in records.values()
                    if manifest.get('analysis', {}).get('semanticProviders')}
        for _ in range(64):
            dependencies = {dependency for manifest in records.values()
                            if manifest['id'] in relevant for dependency in manifest.get('requires', [])}
            if dependencies <= relevant:
                break
            relevant.update(dependencies)
        for manifest_path in paths[1:]:
            root = manifest_path.parent
            manifest = records.get(str(root))
            if manifest is None or manifest['id'] in relevant:
                paths.extend([root, *sorted(islice(root.rglob('*'), 129))])
    return stamp, tuple((str(path), file_stamp(path)) for path in paths)


def make_overlay(snapshot, graph, root, results, bindings, contents, rules, *, projection_counts=None):
    """
    Validate source/provider anchors and create a compact graph delta from typed results.

    snapshot is the selected canonical snapshot path; graph retains raw IDs.
    root is the workspace root; results are typed, provenance-pinned callbacks.
    bindings and contents contain verified declaration spans and file bytes;
    rules pins selected providers. Optional projection_counts is a caller-owned
    dictionary populated by (provider ID, target ID), with raw/accepted/rejected
    fact counts and unique confirmed edge contributions. It describes construction,
    not successful publication. Returns the overlay without changing snapshot bytes.
    Raises ValueError for changed identities, unverified sources or exceeded budgets.
    Raises OSError if the canonical snapshot cannot be read for its content hash.

    Unmapped project and external targets remain bounded symbol references. Only
    unique source locations produce graph edges. Conflicts preserve both facts;
    a compiler binding is static evidence, never a confirmed runtime invocation.
    """
    providers = {f"{item['modId']}/{item['id']}": item for item in rules['semanticProviders']}
    node_ids = {node['id'] for node in graph.get('nodes', [])}
    facts, edges, by_edge, conflicts, scopes = {}, [], {}, [], []
    declarations = DeclarationIndex(bindings, contents)
    verified_hashes = {binding['span']['path']: binding['sourceHash'] for binding in bindings}
    raw_sites = {}
    for raw in graph.get('edges', []):
        site = raw.get('callSite') or (raw.get('evidence') or {}).get('callSite')
        if isinstance(site, dict):
            key = raw.get('source'), raw.get('relation'), site.get('startByte'), site.get('endByte')
            raw_sites.setdefault(key, []).append(raw.get('target'))
    fact_count = 0
    scope_counts, scope_edge_keys = {}, {}
    for result in results:
        provider = providers.get(result.provider_id)
        if provider is None or result.provider_fingerprint != provider['modFingerprint']:
            raise ValueError('Semantic provider changed before publication')
        context = result.context
        if context.snapshot_id != snapshot.parent.name or Path(context.workspace_root).resolve() != root.resolve():
            raise ValueError('Semantic result belongs to another workspace or snapshot')
        scope_key = result.provider_id, context.target_id
        counts = scope_counts.setdefault(scope_key, {'rawFacts': 0, 'projectedFacts': 0,
                                                     'rejectedFacts': 0, 'confirmedEdges': 0})
        counts['rawFacts'] += len(result.facts)
        scope_edges = scope_edge_keys.setdefault(scope_key, set())
        if projection_counts is not None:
            projection_counts[scope_key] = counts
        scopes.append({'providerId': result.provider_id, 'status': result.status,
                       'coverage': {key: asdict(value) for key, value in result.coverage.items()},
                       'engineFingerprint': result.engine_fingerprint,
                       'providerFingerprint': result.provider_fingerprint,
                       'configFingerprint': context.config_fingerprint, 'targetId': context.target_id})
        for fact in result.facts:
            fact_count += 1
            if fact_count > MAX_FACTS:
                raise ValueError('Semantic overlay fact budget exceeded')
            source_id = declarations.resolve(fact.source)
            if source_id not in node_ids:
                counts['rejectedFacts'] += 1
                continue
            if fact.callsite is not None:
                owner = SemanticSymbol('callsite-owner', 'method', 'callsite', fact.callsite)
                if declarations.resolve(owner) != source_id:
                    raise ValueError('Semantic callsite is outside its source declaration')
            for span in (fact.source.source_span, fact.callsite):
                if span is not None and verified_hashes.get(span.path) != context.source_hashes.get(span.path):
                    raise ValueError('Semantic fact source is not the verified captured file')
            target_id = declarations.resolve(fact.target, require_callable=fact.kind == 'call',
                                             require_declaration_name=True) if fact.target else None
            if target_id is not None:
                target_path = fact.target.source_span.path
                if verified_hashes.get(target_path) != context.source_hashes.get(target_path):
                    raise ValueError('Semantic target source is not pinned by the result context')
            target = asdict(fact.target) if fact.target else None
            if target is not None:
                # Engine-local IDs are opaque; avoid collisions with Graphify IDs.
                target['symbol_id'] = 'semantic:' + sha256((result.provider_id + ':' + target['symbol_id']).encode()).hexdigest()[:32]
                target['graph_node_id'] = target_id
            record = {'kind': fact.kind, 'confidence': fact.confidence, 'target': target,
                      'providerId': result.provider_id, 'evidence': fact.evidence,
                      'callsite': asdict(fact.callsite) if fact.callsite else None,
                      'sourceHash': context.source_hashes[fact.source.source_span.path],
                      'runtimeSelectionKnown': False}
            facts.setdefault(source_id, []).append(record)
            counts['projectedFacts'] += 1
            if target_id not in node_ids or fact.confidence != 'confirmed-static' or fact.callsite is None:
                continue
            left, right = declarations.coordinates[fact.callsite.path].span_bytes(fact.callsite)
            relation = 'calls' if fact.kind == 'call' else 'semantic_' + fact.kind
            key = source_id, target_id, relation, fact.callsite.path, left, right
            scope_edges.add(key)
            counts['confirmedEdges'] = len(scope_edges)
            evidence = {'providerId': result.provider_id, 'sourceHash': context.source_hashes[fact.callsite.path],
                        'runtimeSelectionKnown': False, 'callSite': {'startByte': left, 'endByte': right}}
            if key in by_edge:
                by_edge[key]['semanticEvidence'].append(evidence)
                continue
            edge = {'source': source_id, 'target': target_id, 'relation': relation,
                    'confidence': 'confirmed-static', 'line': fact.callsite.start_line + 1,
                    'sourceHash': evidence['sourceHash'], 'callSite': evidence['callSite'],
                    'semanticEvidence': [evidence], 'runtimeSelectionKnown': False,
                    'semanticOnly': True}
            by_edge[key] = edge
            edges.append(edge)
            for raw_target in raw_sites.get((source_id, relation, left, right), []):
                if raw_target != target_id:
                    conflicts.append({'source': source_id, 'graphifyTarget': raw_target,
                                      'semanticTarget': target_id, 'callSite': edge['callSite']})
    result = {'version': VERSION, 'snapshotId': snapshot.parent.name,
              'sourceHash': snapshot_hash(snapshot, file_stamp(snapshot)),
              'rulesFingerprint': rules['fingerprint'], 'facts': facts, 'edges': edges,
              'conflicts': conflicts, 'scopes': scopes}
    result['revision'] = sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_OVERLAY_BYTES:
        raise ValueError('Semantic overlay byte budget exceeded')
    return result


def write_overlay(snapshot, overlay):
    """Atomically publish one host-produced derivative, preserving earlier data on failure."""
    path = overlay_path(snapshot)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex)
    try:
        temporary.write_text(json.dumps(overlay, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_overlay(overlay, ids):
    """Reject damaged or invented fields before projecting an optional persisted derivative."""
    expected = {'version', 'snapshotId', 'sourceHash', 'rulesFingerprint', 'facts',
                'edges', 'conflicts', 'scopes', 'revision'}
    if set(overlay) != expected:
        raise ValueError('Unknown semantic overlay fields')
    payload = {key: value for key, value in overlay.items() if key != 'revision'}
    revision = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if revision != overlay['revision']:
        raise ValueError('Semantic overlay checksum mismatch')
    facts, edges = overlay['facts'], overlay['edges']
    if not isinstance(facts, dict) or set(facts) - ids or not isinstance(edges, list) or len(edges) > MAX_FACTS:
        raise ValueError('Invalid semantic overlay inventory')
    total = 0
    for records in facts.values():
        if not isinstance(records, list):
            raise ValueError('Invalid semantic fact collection')
        total += len(records)
        for fact in records:
            if (set(fact) != {'kind', 'confidence', 'target', 'providerId', 'evidence', 'callsite', 'sourceHash', 'runtimeSelectionKnown'}
                    or fact['kind'] not in {'call', 'reference', 'type', 'definition'}
                    or fact['confidence'] not in {'unknown', 'candidate', 'confirmed-static'}
                    or fact['runtimeSelectionKnown'] is not False):
                raise ValueError('Invalid semantic fact evidence')
            target = fact['target']
            if target is not None:
                symbol = dict(target)
                if symbol.get('source_span') is not None:
                    symbol['source_span'] = SourceSpan(**symbol['source_span'])
                validated = SemanticSymbol(**symbol)
                if not validated.symbol_id.startswith('semantic:') or validated.graph_node_id is not None and validated.graph_node_id not in ids:
                    raise ValueError('Invalid semantic symbol identity')
            elif fact['confidence'] == 'confirmed-static':
                raise ValueError('Confirmed semantic fact has no target')
            if fact['callsite'] is not None:
                SourceSpan(**fact['callsite'])
    if total > MAX_FACTS:
        raise ValueError('Semantic fact budget exceeded')
    for edge in edges:
        if (edge['source'] not in ids or edge['target'] not in ids or edge['runtimeSelectionKnown'] is not False
                or edge['confidence'] != 'confirmed-static' or edge['semanticOnly'] is not True
                or edge['relation'] not in {'calls', 'semantic_reference', 'semantic_type', 'semantic_definition'}):
            raise ValueError('Invalid semantic edge')
        site = edge['callSite']
        if (type(site.get('startByte')) is not int or type(site.get('endByte')) is not int
                or not 0 <= site['startByte'] <= site['endByte']):
            raise ValueError('Invalid semantic callsite')
    if not isinstance(overlay['scopes'], list) or len(overlay['scopes']) > 32 or len(overlay['conflicts']) > MAX_FACTS:
        raise ValueError('Invalid semantic scope inventory')
    for scope in overlay['scopes']:
        for value in scope['coverage'].values():
            SemanticCoverage(**value)


class SemanticOverlayReader:
    """Retain two bounded deltas and shallow projections; unchanged graph facts remain shared internally."""
    def __init__(self):
        """Initialize a small cache owned and synchronized by ProjectStore."""
        self.cache = OrderedDict()
        self.deltas = OrderedDict()

    def project(self, graph, snapshot, mods):
        """Return the effective graph or the identical baseline when optional evidence is unavailable."""
        stamp = semantic_stamp(snapshot, mods)
        if stamp == (0, 0):
            return graph
        source_revision = tuple(snapshot_source_stamp(snapshot))
        key = str(snapshot), source_revision, id(graph), stamp
        if key in self.cache:
            self.cache.move_to_end(key)
            base, projected = self.cache[key]
            if base is graph:
                return projected
        result = self._project(graph, snapshot, mods, stamp)
        # A large source stays available to this request without extending retention.
        if source_revision[1] <= 64 * 1024 * 1024:
            # An integer id alone can be recycled after an owned response dies.
            # Retain the exact base identity within the same two-entry budget.
            self.cache[key] = (graph, result)
            while len(self.cache) > 2:
                self.cache.popitem(last=False)
        return result

    def _validated_delta(self, graph, snapshot, mods, stamp):
        """Cache at most two validated deltas independently of freshly decoded layout identities."""
        files = tuple(sorted({node.get('file') for node in graph['nodes'] if node.get('file')}))
        key = str(snapshot), tuple(snapshot_source_stamp(snapshot)), stamp, files
        ids = {node['id'] for node in graph['nodes']}
        if key in self.deltas:
            self.deltas.move_to_end(key)
            cached = self.deltas[key]
            if cached is None:
                return None
            overlay, required_ids = cached
            return overlay if required_ids <= ids else None
        path = overlay_path(snapshot)
        selected = None
        if 0 < file_stamp(path)[1] <= MAX_OVERLAY_BYTES:
            overlay = json.loads(path.read_text(encoding='utf-8'))
            if (overlay.get('version') == VERSION and overlay.get('snapshotId') == snapshot.parent.name
                    and overlay.get('sourceHash') == snapshot_hash(snapshot, file_stamp(snapshot))):
                rules = active_semantic_rules(mods, inventory={'files': list(files), 'truncated': False})
                if rules['fingerprint'] == overlay.get('rulesFingerprint'):
                    _validate_overlay(overlay, ids)
                    required = set(overlay['facts'])
                    for edge in overlay['edges']:
                        required.update((edge['source'], edge['target']))
                    for records in overlay['facts'].values():
                        for fact in records:
                            target = fact.get('target') or {}
                            if target.get('graph_node_id') is not None:
                                required.add(target['graph_node_id'])
                    selected = overlay, required
        self.deltas[key] = selected
        while len(self.deltas) > 2:
            self.deltas.popitem(last=False)
        return selected[0] if selected else None

    def _project(self, graph, snapshot, mods, stamp):
        """Apply validated evidence to this layout; optional corruption leaves its baseline usable."""
        try:
            overlay = self._validated_delta(graph, snapshot, mods, stamp)
            if overlay is None:
                return graph
            facts, edges = overlay['facts'], overlay['edges']
            nodes = []
            for node in graph['nodes']:
                if node['id'] in facts:
                    nodes.append({**node, 'semanticFacts': deepcopy(facts[node['id']])})
                else:
                    nodes.append(node)
            merged = list(graph['edges'])
            existing = {(item.get('source'), item.get('target'), item.get('relation'),
                         (item.get('callSite') or {}).get('startByte'),
                         (item.get('callSite') or {}).get('endByte')): index for index, item in enumerate(merged)}
            for edge in edges:
                site = edge.get('callSite') or {}
                key = edge['source'], edge['target'], edge['relation'], site.get('startByte'), site.get('endByte')
                if key not in existing:
                    merged.append(deepcopy(edge))
                    existing[key] = len(merged) - 1
                else:
                    index = existing[key]
                    original = merged[index]
                    merged[index] = {**original, 'semanticEvidence': [
                        *deepcopy(original.get('semanticEvidence', [])), *deepcopy(edge['semanticEvidence'])]}
            metadata = {**graph.get('metadata', {}), 'semanticEnrichment': {
                'version': VERSION, 'revision': overlay['revision'], 'status': 'partial',
                'scopes': deepcopy(overlay['scopes']), 'conflicts': deepcopy(overlay['conflicts']),
                'runtimeSelectionKnown': False, 'rawVendorFileMutation': False}}
            return {**graph, 'nodes': nodes, 'edges': merged, 'metadata': metadata}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return graph
