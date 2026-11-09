"""Единый чистый движок: проверяет захваченные факты, не читая диск или UI-проекцию."""
from .architecture_cycles import cycle_components
from .architecture_facts import fingerprint
from .architecture_rules import VERSION, ANALYZER, Rules

MAX_FINDINGS = 1000


def rules_identity(config: Rules) -> str:
    """Привязывает результат к смыслу конфигурации, независимо от порядка списков."""
    layers, rules = [], []
    for layer in config.layers:
        layers.append({'id': layer.id, 'paths': sorted(set(layer.paths)), 'projects': sorted(set(layer.projects))})
    for rule in config.rules:
        item = rule.model_dump()
        for key in ('layers', 'target', 'relations'):
            item[key] = sorted(set(item[key]))
        rules.append(item)
    return fingerprint({'version': config.version, 'pythonRoot': config.pythonRoot, 'exclude': sorted(set(config.exclude)), 'layers': sorted(layers, key=lambda item: item['id']),
                        'rules': sorted(rules, key=lambda item: item['id'])})


def _finding(rule_id: str, edges: list[dict], kind: str, component_identity=None) -> dict:
    """Идентичность не зависит от строк/байтов; доказательство сохраняет исходные позиции."""
    identity = component_identity if component_identity is not None else sorted({(edge['source'], edge['target'], edge['relation']) for edge in edges})
    return {'id': fingerprint([rule_id, kind, identity]), 'ruleId': rule_id, 'kind': kind,
            'path': edges, 'sourceFiles': sorted({edge['source'] for edge in edges})}


def evaluate(config: Rules, facts: dict) -> dict:
    """Нарушение имеет приоритет над неполнотой; сомнительные факты не становятся доказательством."""
    results, findings = [], []
    for rule in sorted(config.rules, key=lambda item: item.id):
        names = sorted(set(rule.layers + rule.target))
        reasons = list(facts['inventoryReasons'])
        compatible = facts.get('analyzerVersion') == ANALYZER
        if not compatible:
            reasons.append('incompatible-analyzer-version')
        selected = set()
        for layer in names:
            selected.update(facts['layers'].get(layer, []))
            reasons.extend(facts['layerReasons'].get(layer, ['missing-layer']))
        for file in sorted(selected):
            if file in facts['errors']:
                reasons.append(file + ':' + facts['errors'][file])
            for relation in rule.relations:
                for reason in facts['completeness'].get(file, {}).get(relation, ['missing-coverage']):
                    reasons.append(file + ':' + relation + ':' + reason)
        sources = set().union(*(set(facts['layers'].get(layer, [])) for layer in rule.layers))
        targets = set().union(*(set(facts['layers'].get(layer, [])) for layer in rule.target)) if rule.target else sources
        edges = []
        ambiguous = any(reason.startswith(('overlapping-selector:', 'unknown-project:', 'ambiguous-project-ownership'))
                        for layer in names for reason in facts['layerReasons'].get(layer, []))
        for edge in facts['edges']:
            if edge['source'] not in sources or edge['target'] not in targets or edge['relation'] not in rule.relations:
                continue
            # Source hash обязателен даже для внутреннего pure-contract клиента.
            source_hash = edge.get('sourceHash')
            if (edge.get('confidence') not in {'declared', 'resolved'} or not source_hash
                    or source_hash != facts['sourceHashes'].get(edge['source'])):
                reasons.append('untrusted-edge')
            elif edge['relation'] == 'code-dependency' and (not edge.get('targetHash')
                    or edge['targetHash'] != facts['sourceHashes'].get(edge['target'])):
                reasons.append('untrusted-target-hash')
            elif edge['source'] in facts['errors'] or edge['target'] in facts['errors'] or ambiguous or not compatible:
                reasons.append('ambiguous-or-stale-evidence')
            else:
                edges.append(edge)
        edges.sort(key=lambda edge: (edge['source'], edge['target'], edge['relation'], edge.get('line') or 0))
        retained, total = [], 0
        remaining = max(0, MAX_FINDINGS - len(findings))
        if rule.kind == 'forbidden':
            pairs = set()
            for edge in edges:
                key = (edge['source'], edge['target'], edge['relation'])
                if key not in pairs:
                    pairs.add(key)
                    total += 1
                    if len(retained) < remaining:
                        retained.append(_finding(rule.id, [edge], rule.kind))
        else:
            for component in cycle_components(edges):
                total += 1
                if len(retained) < remaining:
                    retained.append(_finding(rule.id, component['path'], rule.kind, component['identity']))
        limited = len(retained) != total
        if limited:
            reasons.append('finding-limit')
        findings.extend(retained)
        status = 'violated' if total else ('unknown' if reasons else 'passed')
        results.append({'ruleId': rule.id, 'status': status, 'reasons': sorted(set(reasons))[:100],
                        'reasonsTruncated': len(set(reasons)) > 100, 'findingCount': total,
                        'findingsTruncated': limited, 'selectedFiles': len(selected),
                        'coverage': 'unknown' if reasons else 'complete'})
    counts = {status: sum(item['status'] == status for item in results) for status in ('passed', 'violated', 'unknown')}
    status = 'violated' if counts['violated'] else ('unknown' if counts['unknown'] else 'passed')
    return {'version': VERSION, 'analyzerVersion': ANALYZER, 'rulesFingerprint': rules_identity(config),
            'sourceFingerprint': facts['sourceFingerprint'], 'snapshotId': facts['snapshotId'],
            'sourceKind': facts['sourceKind'], 'status': status, 'counts': counts, 'rules': results,
            'findings': findings, 'findingsTruncated': any(item['findingsTruncated'] for item in results),
            'sourceHashes': facts['sourceHashes'], 'limits': facts['limits'],
            'interpretation': 'Static declarations in the explicit relation/scope; no runtime completeness claim.'}
