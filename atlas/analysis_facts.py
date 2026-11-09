"""Bounded views of captured language/build/framework evidence, shared by all MCP readers."""
from copy import deepcopy
import json
from itertools import islice, chain

PREVIEW_ITEMS = 4
DETAIL_BYTES = 10_000
FACT_FIELDS = ('memberFacts', 'fieldFacts', 'codeProjectFacts', 'testFacts', 'testContainerFacts', 'dataFacts', 'entrypointFacts', 'htmlFacts', 'semanticFacts')
_PRIORITY = ('sourceKind','sourceHash','version','snapshotId','id','nodeId','nodeIds','language','framework','modId',
             'runtimeStatus','declaredStatus','status','label','kind','file','line','containerNodeId','dataSources','declaredRowCount')


def _bounded(value, remaining, depth=0, identity=False, chars=400):
    """Copy a fixed amount of JSON evidence; preserve emitted node IDs and mark omissions."""
    remaining[0] -= 1
    if remaining[0] < 0:
        return None, True
    if isinstance(value, str):
        return (value, False) if identity else (value[:chars], len(value) > chars)
    if isinstance(value, list):
        if depth > 7:
            return [], bool(value)
        result, limited = [], len(value) > 12
        for item in value[:12]:
            if remaining[0] <= 0:
                limited = True; break
            copied, shortened = _bounded(item, remaining, depth+1, identity, chars)
            result.append(copied); limited |= shortened
        return result, limited
    if isinstance(value, dict):
        if depth > 7:
            return {'truncated':True, 'detailsOmitted':True}, True
        result, limited = {}, len(value) > 40
        keys = chain((key for key in _PRIORITY if key in value), (key for key in value if key not in _PRIORITY))
        for key in islice(keys, 40):
            if remaining[0] <= 0:
                limited = True; break
            copied, shortened = _bounded(value[key], remaining, depth+1,
                key.endswith('NodeId') or key.endswith('NodeIds') or key in {'id','nodeId','nodeIds','source','target','graph_node_id','symbol_id'}, chars)
            result[key] = copied; limited |= shortened
        if limited:
            result['truncated'] = True
        return result, limited
    return value, False


def bounded_fact(value, byte_limit=DETAIL_BYTES):
    """Keep an independently bounded section; oversized IDs are omitted, never made into false IDs."""
    for chars, nodes in ((400,240), (160,160), (64,80)):
        result, limited = _bounded(value, [nodes], chars=chars)
        if limited and isinstance(result, dict):
            result['truncated'] = True
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) <= byte_limit:
            return result
    # Even an oversized identifier cannot remove the evidence boundary or invent a shortened ID.
    result = {'truncated':True, 'detailsOmitted':True, 'sourceKind':'snapshot-captured-source'}
    if isinstance(value, dict):
        for key in ('runtimeStatus','declaredStatus','language','framework','status'):
            if isinstance(value.get(key), str):
                result[key] = value[key][:128]
        sources = []
        for source in value.get('dataSources', [])[:PREVIEW_ITEMS]:
            fields = _fields(source, ('kind','label','file','line','nodeIds','status','declaredRowCount'))
            copied, _ = _bounded(fields, [40], chars=160)
            candidate = {**result, 'dataSources':[*sources, copied]}
            if len(json.dumps(candidate, ensure_ascii=False).encode('utf-8')) > byte_limit:
                break
            sources.append(copied)
        if sources:
            result['dataSources'] = sources
    return result


def _fields(value, fields):
    """Select named small facts without copying unrequested data trees."""
    return {key: value[key] for key in fields if key in value}


def analysis_preview(node):
    """Expose test intent, annotations, providers and build boundaries without heavy row trees."""
    result = {}
    if node.get('htmlFacts'):
        result['html'] = bounded_fact(node['htmlFacts'], 4500)
    if node.get('entrypointFacts'):
        entries = node['entrypointFacts']
        result['entrypoints'] = bounded_fact({'items': [_fields(entry,
            ('id','kind','label','route','httpMethod','mcpType','operationName','uri','handlerNodeIds',
             'sourceKind','runtimeStatus','modId','file','line','unresolved')) for entry in entries[:PREVIEW_ITEMS]],
            'total': len(entries), 'truncated': len(entries) > PREVIEW_ITEMS}, 3500)
    project = node.get('codeProjectFacts') or {}
    if project:
        result['project'] = bounded_fact(_fields(project, ('id','label','path','kind','language','confidence','modId',
            'modFingerprint','sourceKind','ruleId','manifest','buildManifest','buildProjectId','buildProject')), 3500)
    test = node.get('testFacts') or {}
    if test:
        summary = _fields(test, ('id','label','framework','language','modId','tags','declaredStatus','containerNodeId',
                                 'disabledReason','runtimeStatus','sourceKind','confidence'))
        sources = test.get('dataSources', [])
        summary['dataSources'] = [_fields(item, ('id','strategy','kind','label','file','line','nodeIds','status','declaredRowCount','completeness','rowsTruncated'))
                                  for item in sources[:PREVIEW_ITEMS]]
        summary['dataSourceCount'] = len(sources)
        summary['fixtureCount'] = len(test.get('fixtures', []))
        summary['metadata'] = test.get('metadata', [])[:PREVIEW_ITEMS]
        summary['detailsAvailable'] = True
        summary['preview'] = True
        summary['truncated'] = len(sources) > PREVIEW_ITEMS or len(test.get('metadata', [])) > PREVIEW_ITEMS
        result['test'] = bounded_fact(summary, 4500)
    container = node.get('testContainerFacts') or {}
    if container:
        result['testContainer'] = _fields(container, ('version', 'nodeId', 'declaredTestCount',
            'runtimeStatus', 'sourceKind', 'discoveryStatus', 'truncated'))
    attributes = node.get('attributeFacts') or {}
    if attributes:
        names = []
        for group in attributes.get('groups', [])[:8]:
            for item in group.get('attributes', [])[:PREVIEW_ITEMS]:
                if len(names) >= PREVIEW_ITEMS:
                    break
                names.append(_fields(item, ('name','qualifiedName','resolution')) | {'scope':group.get('scope')})
        total = sum(len(group.get('attributes', [])) for group in attributes.get('groups', []))
        result['annotations'] = bounded_fact({'language':attributes.get('language'), 'status':attributes.get('status'),
                                 'items':names, 'total':total, 'detailsAvailable':True,
                                 'truncated':bool(attributes.get('truncated') or total > len(names))}, 2500)
    language = (node.get('memberFacts') or {}).get('language') or test.get('language') or project.get('language') or attributes.get('language')
    if language:
        result['language'] = language
    if result:
        result.update(version='atlas-analysis-facts-v1', sourceKind='snapshot-captured-source')
    return result


def analysis_context(graph):
    """Describe the captured provider inventory, never the currently enabled runtime or executed plugins."""
    rules = graph.get('metadata', {}).get('analysisMods') or {}
    mods = rules.get('mods', [])
    return {'version':rules.get('version'), 'fingerprint':rules.get('fingerprint'),
            'status':'captured' if rules.get('fingerprint') else 'unavailable',
            'sourceKind':'snapshot-captured-source', 'mods':deepcopy(mods[:16]),
            'totalMods':len(mods), 'truncated':len(mods)>16}


def description_analysis_facts(node, graph):
    """Separate snapshot-only mod evidence from the description's current working-tree flow."""
    result = {'analysisFacts':analysis_preview(node), 'analysisContext':analysis_context(graph)}
    for key in FACT_FIELDS:
        if key in node:
            result[key] = bounded_fact(node[key], 5000)
    return result


def searchable_fact_strings(node):
    """Yield folded strings from node's captured facts with the existing visit/depth/collection limits.

    Excluded source bodies and hashes never enter search; strings are truncated
    before case folding so indexed and uncached queries see the same text.
    """
    candidates = [node.get('codeProjectFacts'), node.get('testFacts'), node.get('attributeFacts'), node.get('memberFacts'), node.get('entrypointFacts'), node.get('htmlFacts')]
    remaining = 320
    pending = [(value, 0) for value in candidates if value]
    while pending and remaining:
        value, depth = pending.pop(); remaining -= 1
        if isinstance(value, str):
            yield value[:1000].casefold()
        elif depth < 6 and isinstance(value, dict):
            # Rows/source bodies can be large; named inputs and provider links are the search surface.
            pending.extend((item, depth+1) for key, item in islice(value.items(), 40)
                           if key not in {'rows','expression','sourceHash','modFingerprint','fingerprint','sourceKind'})
        elif depth < 6 and isinstance(value, list):
            pending.extend((item, depth+1) for item in value[:20])


def matches_analysis_facts(node, needle):
    """Return whether the already-folded needle occurs in node's bounded captured fact strings."""
    return any(needle in value for value in searchable_fact_strings(node))
