"""Bounded declaration inventories and resolved file/folder dependencies, without filesystem scans."""

from collections import defaultdict
from pathlib import PurePosixPath
import networkx as nx

from .organization_policy import VERSION, score_organization
from .code_health_calls import confirmed_call_edge
from .organization_coverage import file_coverage, folder_coverage, coverage_summary, coverage_fact


def source_file(node: dict) -> str:
    """Use project-relative file facts, excluding synthetic/external paths from organization counts."""
    if node.get('kind') == 'external':
        return ''
    name = str(node.get('file') or '').replace('\\', '/')
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ':' in name or '..' in path.parts:
        return ''
    return path.as_posix()


def cycle_members(graph) -> set:
    """Return observed multi-file cycles; calls within one file/folder are not architecture cycles."""
    result = set()
    for members in nx.strongly_connected_components(graph):
        if len(members) > 1:
            result.update(members)
    return result


def parent_folder(filename: str) -> str:
    """Folders are direct lexical parents, independent of any language or preferred tree depth."""
    return str(PurePosixPath(filename).parent)


def _declaration_index(nodes: list[dict]) -> tuple[dict, dict]:
    """Index paths once per distinct file and count each measured declaration once."""
    records = defaultdict(list)
    paths, by_id, seen = {}, {}, set()
    for node in nodes:
        if node.get('kind') == 'external':
            by_id[node['id']] = ''
            continue
        raw_path = str(node.get('file') or '')
        if raw_path not in paths:
            paths[raw_path] = source_file(node)
        filename = paths[raw_path]
        by_id[node['id']] = filename
        metrics = node.get('complexity') or {}
        if not filename or not metrics or metrics.get('reason') == 'declaration has no body':
            continue
        identity = node.get('healthIdentity') or node['id']
        if identity not in seen:
            seen.add(identity)
            records[filename].append(node)
    return records, by_id


def _dependency_topology(by_id: dict, selected: set, edges: list[dict], source_nodes: dict) -> tuple:
    """Build topology and uncertain sources in one source-bound pass over call edges."""
    file_graph = nx.DiGraph()
    file_graph.add_nodes_from(selected)
    uncertain_sources = set()
    external_targets = {identifier for identifier, node in source_nodes.items() if node.get('kind') == 'external'}
    for edge in edges:
        if (edge.get('relation') or edge.get('type')) != 'calls':
            continue
        origin = by_id.get(edge.get('source'))
        if origin not in selected:
            continue
        target = by_id.get(edge.get('target'))
        confirmed = confirmed_call_edge(edge, source_nodes.get(edge.get('source')))
        if not confirmed or (not target and edge.get('target') not in external_targets):
            uncertain_sources.add(origin)
        if confirmed and target and target != origin:
            file_graph.add_edge(origin, target)
    folders = {filename: parent_folder(filename) for filename in file_graph}
    folder_graph = nx.DiGraph()
    folder_graph.add_nodes_from(folders[name] for name in selected)
    for origin, target in file_graph.edges:
        if folders[origin] != folders[target]:
            folder_graph.add_edge(folders[origin], folders[target])
    return file_graph, folder_graph, folders, uncertain_sources


def confirmed_call(edge: dict, source_node: dict | None = None) -> bool:
    """Exclude hypotheses and stale source evidence from observed file/folder topology."""
    if (edge.get('relation') or edge.get('type')) != 'calls':
        return False
    return confirmed_call_edge(edge, source_node)


def _source_reliability(items: list[dict], source: dict, complete: bool) -> tuple[bool, bool]:
    """Local syntax completeness never depends on resolving a neighbouring method's calls."""
    parsed = source.get('status', 'complete') == 'complete' and not source.get('truncated')
    file_proven = source.get('status') == 'complete' and not source.get('truncated')
    inventory = (complete or file_proven) and parsed and bool(items)
    local = inventory and all(node['complexity'].get('status') == 'complete'
                              and node['complexity'].get('statementCount') is not None
                              and node['complexity'].get('branchingCount') is not None for node in items)
    return inventory, local


def organization_profiles(nodes: list[dict], edges: list[dict], dependencies: dict,
                          *, files: dict | None = None, complete: bool = True) -> dict:
    """Measure independent facts in a bounded selected-source scope, without source rereads.

    Complete numeric O retains its existing formula. A missing target can affect
    incoming edges and cycles anywhere in scope, so topology fails closed while
    per-file inventory/local/outgoing facts retain their own coverage.
    """
    records, by_id = _declaration_index(nodes)
    source_nodes = {node['id']: node for node in nodes}
    selected = {name for name, facts in files.items() if not facts.get('empty')} if files is not None else set(records)
    file_graph, folder_graph, parent_by_file, uncertain_sources = _dependency_topology(by_id, selected, edges, source_nodes)
    file_cycles, folder_cycles = cycle_members(file_graph), cycle_members(folder_graph)
    inventory, folders = {}, {}
    for filename in sorted(selected):
        items = records[filename]
        known = [node for node in items if node['complexity'].get('status') == 'complete']
        source = (files or {}).get(filename, {})
        source_hashes = {node['complexity'].get('sourceHash') for node in items if node['complexity'].get('sourceHash')}
        source_hash = source.get('sourceHash') or (next(iter(source_hashes)) if len(source_hashes) == 1 else None)
        inventory_complete, local_complete = _source_reliability(items, source, complete)
        outgoing_complete = inventory_complete and filename not in uncertain_sources
        outgoing_complete &= all(dependencies.get(node['id'], {}).get('status') == 'complete' for node in items)
        metrics = {'declarations': len(items), 'knownDeclarations': len(known),
                   'statements': sum(node['complexity'].get('statementCount') or 0 for node in items),
                   'branches': sum(node['complexity'].get('branchingCount') or 0 for node in items),
                   'outgoingFiles': file_graph.out_degree(filename), 'incomingFiles': file_graph.in_degree(filename),
                   'inCycle': filename in file_cycles}
        coverage = file_coverage(filename, inventory=inventory_complete, local=local_complete,
                                 outgoing=outgoing_complete, topology=False, source_hash=source_hash)
        inventory[filename] = (metrics, coverage)
        parent = parent_by_file[filename]
        folder = folders.setdefault(parent, {'path': parent, 'files': 0, 'tinyFiles': 0, 'internalLinks': 0,
                                             'depth': len(PurePosixPath(parent).parts),
                                             'outgoingFolders': folder_graph.out_degree(parent),
                                             'inCycle': parent in folder_cycles})
        folder['files'] += 1
        folder['tinyFiles'] += local_complete and metrics['statements'] <= 3 and metrics['declarations'] <= 1
    # This is deliberately one global selected-scope fence. Unknown targets have
    # no proven locality; a per-folder assumption could falsely prove no cycle.
    topology_complete = complete and all(coverage['outgoing']['status'] == 'complete'
                                          for _, coverage in inventory.values())
    all_local_complete = all(coverage['local']['status'] == 'complete' for _, coverage in inventory.values())
    folder_members = defaultdict(list)
    for filename, (_, coverage) in inventory.items():
        local_complete = coverage['local']['status'] == 'complete'
        updated = file_coverage(filename, inventory=coverage['inventory']['status'] == 'complete',
                                local=local_complete, outgoing=coverage['outgoing']['status'] == 'complete',
                                topology=topology_complete, source_hash=coverage['local'].get('sourceHash'))
        # Tiny classification anywhere in the selected graph can affect the
        # connected component, including a member in another folder.
        if not all_local_complete:
            updated['fragmentation'].update(status='partial', reason='incomplete-local-inventory-in-scope')
        coverage.update(updated)
        folder_members[parent_by_file[filename]].append(coverage)
    for parent, folder in folders.items():
        folder['coverage'] = folder_coverage(parent, folder_members[parent])
        # The explicit file map enumerates the selected names even when a
        # particular parser is unavailable. This does not count the filesystem.
        folder['coverage']['inventory'] = coverage_fact(files is not None or complete,
            'incomplete-selected-file-inventory', 'selected-files:' + parent)
        folder['complete'] = all(fact['status'] == 'complete' for fact in folder['coverage'].values())
    for origin, target in file_graph.edges:
        parent = parent_by_file[origin]
        if target in selected and parent == parent_by_file[target]:
            folders[parent]['internalLinks'] += 1
    tiny = {name for name, (metrics, coverage) in inventory.items()
            if coverage['local']['status'] == 'complete' and 0 < metrics['declarations'] <= 1 and metrics['statements'] <= 3}
    connected_tiny = {}
    for component in nx.weakly_connected_components(file_graph.subgraph(tiny)):
        for name in component:
            connected_tiny[name] = len(component)
    result = {}
    for filename, (metrics, coverage) in inventory.items():
        metrics['connectedTinyFiles'] = connected_tiny.get(filename, 0)
        folder = folders[parent_by_file[filename]]
        reliable = folder['complete'] and all(fact['status'] == 'complete' for fact in coverage.values())
        result[filename] = score_organization(filename, metrics, folder, reliable, coverage=coverage)
    return result


def organization_summary(profiles: dict) -> dict:
    """Expose totals and five hotspots per level; do not average repeated method cards as files."""
    rows = list(profiles.values())
    known = [row['score'] for row in rows if row.get('score') is not None]
    folders = {}
    for row in rows:
        folders[row['folder']] = row['folderMetrics']
    ranked = sorted(rows, key=lambda row: (not bool(row['findings']), row['score'] if row['score'] is not None else -1, row['file']))
    return {'version': VERSION, 'status': 'complete' if rows and len(known) == len(rows) else 'partial' if rows else 'unavailable',
            'score': round(sum(known) / len(known), 1) if rows and len(known) == len(rows) else None,
            'coverage': coverage_summary(rows),
            'fileCount': len(rows), 'folderCount': len(folders), 'assessedFiles': len(known),
            'unknownFiles': len(rows) - len(known),
            'scope': 'selected-files; direct folders, not a filesystem inventory',
            'files': [{key: row[key] for key in ('file', 'folder', 'score', 'status', 'metrics', 'coverage') if key in row} for row in ranked[:5]],
            'folders': [{'path': name, **metrics} for name, metrics in sorted(folders.items(), key=lambda pair: (-pair[1]['files'], pair[0]))[:5]],
            'hotspotsTruncated': len(rows) > 5 or len(folders) > 5}
