"""Краткие поля общего снимка для поиска/соседства и печатных Graphify инструментов."""
from copy import deepcopy
from .code_health import compact_health
from .analysis_facts import analysis_preview


def member_preview(node):
    """Описание видимости и до трёх пар доступны сразу; полные данные сохраняет get_node."""
    result = compact_health(node)
    analysis = analysis_preview(node)
    if analysis:
        result['analysisFacts'] = analysis
    if node.get('complexity'):
        result['complexity'] = deepcopy(node['complexity'])
    if node.get('memberFacts'):
        facts = node['memberFacts']
        result['memberFacts'] = {key: deepcopy(facts[key]) for key in ('language','kind','name','ownerNodeId','visibility','visibilityKind','static','readonly','constant','constantKind','sourceKind') if key in facts}
    if node.get('fieldFacts'):
        facts = node['fieldFacts']
        result['fieldFacts'] = {key: facts.get(key) for key in ('kind','name','type','nullable','sourceKind')}
    data = node.get('dataFacts') or node.get('staticData')
    if data:
        pairs = data.get('entries',[])
        result['dataFacts'] = {'kind':data.get('kind'),'initializer':str(data.get('initializer',''))[:180],
            'entries':[{'key':str(e.get('key',''))[:80],'value':str(e.get('value',''))[:120]} for e in pairs[:3]],
            'totalEntries':len(pairs),'sourceKind':data.get('sourceKind'),
            'truncated':bool(data.get('truncated') or len(pairs)>3 or len(data.get('initializer',''))>180
                             or any(len(e.get('key',''))>80 or len(e.get('value',''))>120 for e in pairs[:3]))}
    return result


def printed_member_preview(node):
    """Alphabetically first aliases fit Graphify's 1000-character, ASCII-escaped print budget.

    Full captured facts remain on the node and in the ordinary attributes. This
    deliberately small projection shows binding visibility and one data pair.
    """
    member = node.get('memberFacts')
    result = {}
    test = node.get('testFacts')
    project = node.get('codeProjectFacts')
    if test or project:
        result['atlas.0.analysis'] = {
            'language': (test or project).get('language'),
            **({'framework':test.get('framework'), 'runtimeStatus':test.get('runtimeStatus'),
                'dataSources':len(test.get('dataSources', []))} if test else {}),
            **({'buildManifest':str(project.get('buildManifest', project.get('manifest', '')))[:80]} if project else {})}
    if node.get('complexity'):
        result['atlas.0.complexity'] = {key: node['complexity'].get(key) for key in
            ('version', 'status', 'score', 'maxNestingDepth', 'averageNestingDepth', 'branchingCount', 'loopCount', 'sourceKind')}
    if node.get('health'):
        result['atlas.0.health'] = {key: node['health'].get(key) for key in ('version', 'status', 'score', 'documentationScore', 'verdict')}
    if member:
        result['atlas.0.member'] = {key: member[key] for key in ('language', 'visibility', 'visibilityKind', 'static', 'readonly', 'constant') if key in member}
    data = node.get('dataFacts') or node.get('staticData')
    if data:
        entries = data.get('entries', [])
        result['atlas.1.data'] = {'kind': data.get('kind'), 'initializer': str(data.get('initializer', ''))[:30],
                                'entries': [{'key': str(e.get('key', ''))[:20], 'value': str(e.get('value', ''))[:20]} for e in entries[:1]],
                                'truncated': bool(data.get('truncated') or len(entries) > 1 or len(data.get('initializer', '')) > 30
                                                  or any(len(e.get('key', '')) > 20 or len(e.get('value', '')) > 20 for e in entries[:1]))}
    comment = node.get('comment') or (node.get('fieldFacts') or {}).get('description') or (node.get('callableFacts') or {}).get('description')
    if comment:
        result['atlas.2.comment'] = {'text': comment[:24], 'scope': node.get('commentScope', 'declaration'), 'truncated': len(comment) > 24}
    return result
