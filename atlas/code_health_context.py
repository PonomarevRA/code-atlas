"""Bind current health declarations to verified topology before scoring file/folder organization."""

from .code_health import calculate_health
from .code_organization import organization_profiles, organization_summary


def apply_source_context(methods: list[dict], graph: dict, local_calls, file_analysis: dict,
                         filenames, *, source_kind: str, locale: str, complete: bool) -> dict:
    """Score current/draft files without promoting stale snapshot calls to current facts.

    Unchanged executable stamps permit snapshot topology reuse. New bare-name
    calls use the source binder's unique lexical targets. Other calls remain
    unknown in dependency profiles and therefore in organization scores.
    """
    original = {node['id']: node for node in graph.get('nodes', [])}
    current_ids = {method['id'] for method in methods}
    verified = {method['id'] for method in methods if method.get('healthSourceStamp') and
                original.get(method['id'], {}).get('healthSourceStamp') == method['healthSourceStamp']}
    edges = [edge for edge in graph.get('edges', []) if edge.get('source') in verified]
    identities = {method['healthIdentity']: method['id'] for method in methods}
    for left, right in local_calls.edges:
        edges.append({'source': identities[left], 'target': identities[right], 'relation': 'calls'})
    targets = [{'id': node['id'], 'file': node.get('file', ''), 'kind': node.get('kind')} for node in graph.get('nodes', [])
               if node['id'] not in current_ids]
    source_hashes = {}
    for method in methods:
        source_hashes[method['file']] = method.get('complexity', {}).get('sourceHash')
    profiles = organization_profiles([*methods, *targets], edges,
                {method['id']: method['health']['dependencies'] for method in methods},
                files={name: {**file_analysis.get(name, {'status': 'unavailable'}),
                              'sourceHash': source_hashes.get(name)}
                       for name in filenames},
                complete=complete)
    for method in methods:
        method['health'] = calculate_health(method, method['health']['dependencies'],
                                           organization=profiles.get(method['file']), locale=locale)
        method['health'].update(sourceKind=source_kind,
                                assessmentKind='measured' if source_kind == 'working-tree' else 'draft-analysis')
    return organization_summary(profiles)
