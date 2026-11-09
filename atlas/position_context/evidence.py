"""Bounded direct-caller AST observations, not interprocedural nullability proofs."""

from copy import deepcopy
from ..execution_path import _bind_events, _edge_fact
from ..source_provenance import source_provenance
from .source import literal_nulls, relative_name, MAX_DESCRIPTIONS


def callers(graph, nodes, anchor, analysis, descriptions, direction):
    """Read at most four graph callers using the same five-file/source-byte request budget."""
    if anchor is None or direction == 'outgoing':
        return {'callers': [], 'callersStatus': 'not-requested', 'callersTruncated': False}
    ids, seen = [], set()
    for edge in graph['edges']:
        if isinstance(edge, dict) and edge.get('target') == anchor and edge.get('relation') in ('calls', 'dispatches_to'):
            source = edge.get('source')
            if isinstance(source, str) and source in nodes and source not in seen:
                ids.append(source); seen.add(source)
                if len(ids) > 4:
                    break
    output, used, binding_limited, description_limited = [], len(descriptions), False, False
    for raw_id in ids[:4]:
        node = nodes[raw_id]
        description = descriptions.get(raw_id)
        if description is None and used < MAX_DESCRIPTIONS:
            copy = dict(node)
            filename = relative_name(analysis.root, node.get('file') or node.get('source_file'))
            if filename:
                copy['file'] = filename
            description = analysis.describe(copy); used += 1
        if description is None:
            description_limited = True
            output.append({'nodeId': raw_id, 'status': 'unknown', 'reason': 'description-budget', 'events': [],
                           'literalNullArguments': [], 'literalNullStatus': 'unavailable'})
            continue
        description = deepcopy(description)
        outgoing, caller_limited = [], False
        for edge in graph['edges']:
            if (isinstance(edge, dict) and edge.get('source') == raw_id
                    and isinstance(edge.get('target'), str) and edge['target'] in nodes
                    and edge.get('relation') in ('calls', 'dispatches_to')):
                if len(outgoing) >= 240:
                    caller_limited = binding_limited = True
                    break
                outgoing.append(_edge_fact(edge))
        _bind_events(description, outgoing, nodes)
        provenance = source_provenance(graph, node, description, analysis.snapshot_id)
        events = [event for event in description['events'] if anchor in event.get('targetNodeIds', [])]
        nulls = literal_nulls(analysis, description.get('file', ''), description)
        matching_nulls = []
        for argument in nulls['literalNullArguments']:
            # A line/name cannot distinguish sibling, nested or chained calls.
            # The complete exclusive-end AST span identifies the actual occurrence.
            matches = [event for event in events if all(event.get(key) == value for key,value in argument['callSite'].items())]
            if matches:
                argument = dict(argument, targetNodeIds=sorted({target for event in matches for target in event['targetNodeIds']}),
                                requestedTargetId=anchor,
                                bindingStatus='candidate', bindingProvenance='existing graph-to-source event binder')
                matching_nulls.append(argument)
        output.append({'nodeId': raw_id, 'requestedTargetId': anchor,
                       'status': 'observed' if description.get('sourceStatus') == 'parsed' else 'unknown',
                       'sourceProvenance': provenance, 'events': events[:8],
                       'guards': (description.get('controlFlow') or {}).get('branches', [])[:8],
                       'unresolved': description.get('unresolved', [])[:8],
                       'unsupported': (description.get('controlFlow') or {}).get('unsupported', [])[:8],
                       'controlFlowTruncated': (description.get('controlFlow') or {}).get('truncated', False),
                       'literalNullArguments': matching_nulls, 'literalNullStatus': nulls['literalNullStatus'],
                       'bindingTruncated': caller_limited,
                       'truncated': (caller_limited or len(events) > 8 or nulls['literalNullArgumentsTruncated']
                                     or bool((description.get('controlFlow') or {}).get('truncated'))
                                     or bool(description.get('eventsTruncated'))
                                     or len(description.get('unresolved', [])) > 8
                                     or len((description.get('controlFlow') or {}).get('unsupported', [])) > 8)})
    return {'callers': output, 'callersStatus': 'observed', 'callersTruncated': binding_limited or len(ids) > 4 or description_limited}
