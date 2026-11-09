"""
Visited-declaration infrastructure projection without mutating the captured graph.
"""
from copy import deepcopy
from pathlib import Path
import time

from .architecture_http import http_facts
from .execution_path import ExecutionPathError
from .execution_path_source import SourceAnalysis
from .infrastructure.index import infrastructure_index
from .project_scope_context import ProjectScopeContext
from .source_viewer import read_node_source, SourceError
from .system_architecture import _identifier

MAX_VISITS, MAX_RESOURCES, MAX_EDGES, MAX_EVIDENCE = 64, 240, 1500, 12


def _evidence(fact: dict, raw_id: str, repository_id: str) -> list[dict]:
    """
    Project at most 12 public source references from one trusted infrastructure fact.

    Args:
        fact: Resolved provider fact; expressions and configuration bodies are omitted.
        raw_id: Visited declaration that exposed this lexical fact.
        repository_id: Registered primary repository identity attached to each reference.
    Returns:
        Relative file/line/hash references without raw source or absolute paths.
    """
    references = [{'file': fact.get('file'), 'line': fact.get('line')}]
    references.extend(fact.get('evidence', []))
    result = []
    for item in references:
        filename = str(item.get('file') or '')
        path = Path(filename)
        if not filename or path.is_absolute() or '..' in path.parts or '\\' in filename:
            continue
        source_hash = fact.get('sourceHash') if filename == fact.get('file') else item.get('sourceHash')
        reference = {'file': filename, 'line': item.get('line'), 'sourceHash': source_hash,
                     'rawNodeId': raw_id, 'repositoryId': repository_id, 'type': fact.get('basis'),
                     'confidence': fact.get('status', 'unknown')}
        if reference not in result:
            result.append(reference)
        if len(result) >= MAX_EVIDENCE:
            break
    return result


def _owner(view: dict, scope: ProjectScopeContext, raw: dict, repository: dict) -> str:
    """
    Find exact federation ownership, adding an explicit unknown source scope if needed.

    Args:
        view: Request-local copied architecture; only a missing source-scope node is appended.
        scope: Exact primary graph ownership prepared for this request.
        raw: Validated primary raw declaration.
        repository: Registered primary id; no project is inferred from its display name.
    Returns:
        Existing architecture project ID or a stable file-scoped unknown-owner ID.
    """
    code_project_id = scope.owners.get(raw['id'], {}).get('codeProjectId')
    for node in view['nodes']:
        belongs = node.get('repositoryId') == repository['id']
        project_matches = node.get('originalCodeProjectId') == code_project_id
        if belongs and node.get('kind') == 'project' and project_matches:
            return node['id']
    owner_id = _identifier('architecture-source-scope', repository['id'], raw.get('file') or raw['id'])
    if not any(node['id'] == owner_id for node in view['nodes']):
        view['nodes'].append({'id': owner_id, 'kind': 'code-scope', 'label': 'Source scope',
            'repositoryId': repository['id'], 'repositoryIds': [repository['id']],
            'identityScope': 'source-file', 'ownershipKnown': False, 'runtimeSelectionKnown': False})
    return owner_id


def _resource(fact: dict, owner_id: str, repository_id: str) -> dict:
    """
    Keep an owner-local logical resource without inferring a deployed database/broker.

    Args:
        fact: Trusted provider fact with a safe logical name or an explicit unknown destination.
        owner_id: Exact architecture owner; all unknown bindings remain within this scope.
        repository_id: Primary repository identity for navigation and provenance.
    Returns:
        Public resource DTO; configuration candidates remain alternatives, never binding edges.
    """
    kind, resource_kind = fact['kind'], fact.get('resourceKind', 'unknown')
    name = fact.get('name')
    identity = [owner_id, kind, resource_kind, name or fact['id']]
    if kind == 'http':
        identity.append(fact.get('scheme'))
    resource_id = _identifier('architecture-resource', *identity)
    return {'id': resource_id, 'kind': kind, 'dependencyKind': kind, 'resourceKind': resource_kind,
        'label': name or f'{resource_kind} · unknown', 'logicalName': name, 'ownerId': owner_id,
        'repositoryId': repository_id, 'repositoryIds': [repository_id], 'identityScope': 'owner-resource',
        'host': fact.get('host'), 'scheme': fact.get('scheme'), 'status': fact.get('status', 'unknown'),
        'configurationBindingKnown': fact.get('configurationBindingKnown', False),
        'configurationCandidates': deepcopy(fact.get('configurationCandidates', [])[:12]),
        'configurationCandidatesTruncated': fact.get('configurationCandidatesTruncated', False),
        'runtimeSelectionKnown': False,
        'proof': {'basis': fact.get('basis'), 'provider': fact.get('provider'), 'sourceKind': 'working-tree',
                  'runtimeSelectionKnown': False}}


def _append_fact(fact, raw_id, owner_id, repository_id, resources, edges) -> bool:
    """
    Accept one unique fact, enforcing resource/edge caps before either endpoint is published.

    Args:
        fact: Trusted provider fact; duplicate fact IDs are ignored regardless of visit count.
        raw_id: Declaration exposing the fact, used only in evidence.
        owner_id: Architecture owner selected by exact graph ownership.
        repository_id: Registered primary repository identity.
        resources: Request-local accepted resource dictionary, mutated only on acceptance.
        edges: Request-local accepted fact dictionary, mutated only on acceptance.
    Returns:
        True when a hard cap omitted this fact; False for accepted/duplicate/unsupported facts.
    """
    if fact.get('kind') not in {'database', 'kafka', 'rabbit', 'http'} or fact['id'] in edges:
        return False
    node = _resource(fact, owner_id, repository_id)
    resource_limit = node['id'] not in resources and len(resources) >= MAX_RESOURCES
    if resource_limit or len(edges) >= MAX_EDGES:
        return True
    resources.setdefault(node['id'], node)
    source_id, target_id = owner_id, node['id']
    if fact.get('operation') == 'consumes':
        source_id, target_id = target_id, source_id
    edges[fact['id']] = {'id': fact['id'], 'factId': fact['id'], 'source': source_id, 'target': target_id,
        'relation': fact['operation'], 'operation': fact['operation'], 'weight': 1,
        'evidence': _evidence(fact, raw_id, repository_id),
        'evidenceTruncated': len(fact.get('evidence', [])) + 1 > MAX_EVIDENCE,
        'status': fact.get('status', 'unknown'), 'runtimeSelectionKnown': False, 'proof': node['proof']}
    return False


def _fence_sources(repository: dict, sources: dict) -> None:
    """
    Reject source/configuration mutation across the already bounded infrastructure corpus.

    Args:
        repository: Pinned primary root and snapshotId used by the protected source reader.
        sources: At most the existing index corpus budget, including configuration alternatives.
    Raises:
        ExecutionPathError:409 if any previously read source disappeared or changed bytes.
    """
    for filename, indexed in sources.items():
        try:
            current = read_node_source(repository['path'], {'file': filename}, repository['snapshotId'], include_syntax=False)
        except SourceError as error:
            raise ExecutionPathError('Architecture source changed; retry the view.', 409) from error
        if current['sourceByteHash'] != indexed['sourceByteHash']:
            raise ExecutionPathError('Architecture source changed; retry the view.', 409)


def enrich_architecture(view: dict, repository: dict, visited_ids: list[str], *,
                        index_builder=infrastructure_index, analysis_factory=SourceAnalysis) -> dict:
    """
    Add bounded visited infrastructure to a copy of the existing complete federation.

    Args:
        view: Complete existing federation DTO; its core IDs and graph remain unchanged.
        repository: Pinned primary id/path/graph/snapshotId from ProjectStore.federation_snapshots.
        visited_ids: At most 64 primary raw IDs; repeated visits cannot increase fact multiplicity.
        index_builder: Trusted injectable shared InfrastructureIndex constructor/cache.
        analysis_factory: Trusted injectable declaration parser with existing source budgets.
    Returns:
        Existing DTO plus architectureVersion 2 and explicit exploration coverage/provenance.
    Raises:
        ExecutionPathError: 404 for non-primary raw IDs, 413 for visit overflow, 409 for source mutation.
        ImportError: An unavailable source parser remains an explicit caller-visible failure.
    """
    if len(visited_ids) > MAX_VISITS:
        raise ExecutionPathError('Architecture visit limit exceeded.', 413)
    unique = sorted(set(visited_ids))
    raw_nodes = {node['id']: node for node in repository['graph'].get('nodes', [])}
    if any(node_id not in raw_nodes for node_id in unique):
        raise ExecutionPathError('Architecture visits must reference primary snapshot raw nodes.', 404)
    result = deepcopy(view)
    exploration = {'visitedNodeIds': unique, 'appliedFactIds': [], 'addedNodes': 0, 'addedEdges': 0,
        'rejectedVisits': [], 'processedNodeIds': [], 'unprocessedNodeIds': unique,
        'consideredFactCount': 0, 'omittedFactCount': 0,
        'coverage': {}, 'limited': False, 'runtimeSelectionKnown': False}
    result.update(architectureVersion=2, exploration=exploration)
    if not unique:
        return result
    started = time.monotonic()
    scope = ProjectScopeContext(repository['path'], repository['graph'])
    index = index_builder(repository['path'], repository['graph'], repository['snapshotId'])
    exploration['coverage'] = deepcopy(index.coverage)
    exploration['coverage']['scope'] = 'visited-declaration-lexical-including-closures'
    analysis = analysis_factory(repository['path'], repository['snapshotId'], MAX_VISITS, 8 * 1024 * 1024, 160)
    http_by_file, resources, edges = {}, {}, {}
    considered_ids = set()
    for raw_id in unique:
        if time.monotonic() - started > 35:
            exploration['limited'] = True
            break
        exploration['processedNodeIds'].append(raw_id)
        raw = raw_nodes[raw_id]
        description = analysis.describe(raw)
        if description.get('sourceStatus') != 'parsed' or description.get('kind') == 'class':
            exploration['rejectedVisits'].append({'nodeId': raw_id, 'reason': 'declaration-unavailable-or-not-callable'})
            continue
        filename = raw.get('file')
        source = index.sources.get(filename)
        byte_hash = description.get('sourceByteHash', description.get('sourceHash'))
        if not source or source.get('sourceByteHash') != byte_hash:
            exploration['rejectedVisits'].append({'nodeId': raw_id, 'reason': 'source-revision-mismatch'})
            continue
        facts = index.for_node(raw, description, limit=None)
        if filename not in http_by_file:
            try:
                http_by_file[filename] = http_facts(source)
            except (ImportError, ValueError, RecursionError):
                http_by_file[filename] = []
                exploration['coverage']['httpUnavailableFiles'] = exploration['coverage'].get('httpUnavailableFiles', 0) + 1
        for fact in http_by_file[filename]:
            if description['line'] <= fact['line'] <= description['endLine']:
                facts.append(fact)
        if not facts:
            continue
        owner_id = _owner(result, scope, raw, repository)
        for fact in facts:
            if fact['id'] in considered_ids:
                continue
            considered_ids.add(fact['id'])
            exploration['consideredFactCount'] += 1
            omitted = _append_fact(fact, raw_id, owner_id, repository['id'], resources, edges)
            exploration['omittedFactCount'] += int(omitted)
            exploration['limited'] |= omitted
    # Configurations can determine names/bindings too, so fencing only visited files is insufficient.
    _fence_sources(repository, index.sources)
    result['nodes'].extend(resources[key] for key in sorted(resources))
    result['edges'].extend(edges[key] for key in sorted(edges))
    exploration.update(appliedFactIds=sorted(edges), addedNodes=len(result['nodes']) - len(view['nodes']), addedEdges=len(edges))
    processed = set(exploration['processedNodeIds'])
    exploration['unprocessedNodeIds'] = [node_id for node_id in unique if node_id not in processed]
    exploration['limited'] |= bool(index.coverage.get('limited'))
    incomplete_visits = exploration['limited'] or bool(exploration['rejectedVisits'])
    incomplete_http = bool(exploration['coverage'].get('httpUnavailableFiles'))
    exploration['coverage']['complete'] = bool(index.coverage.get('complete')) and not incomplete_visits and not incomplete_http
    result['counts'] = {**result.get('counts', {}), 'nodes': len(result['nodes']), 'edges': len(result['edges'])}
    result['limits'] = {**result.get('limits', {}), 'maxVisitedNodes': MAX_VISITS,
        'maxExplorationResources': MAX_RESOURCES, 'maxExplorationEdges': MAX_EDGES}
    return result
