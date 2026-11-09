"""Explainable local readability, separate from semantic review and the historical H policy."""

from functools import lru_cache
import json
from math import isfinite

from .runtime_paths import resource_root


@lru_cache(maxsize=1)
def readability_policy():
    """Load the shipped heuristic; research motivates review dimensions, not these numeric weights."""
    path = resource_root() / 'resources/code-health/policies/atlas-readability-v1.json'
    return json.loads(path.read_text(encoding='utf-8'))


def assess_readability(metrics: dict, findings: list[dict]):
    """Score complete local syntax independently of graph resolution; never auto-approve semantics.

    Related boolean penalties use their maximum, avoiding double charging the
    same expression. Documentation and token volume do not add rewards/penalties.
    """
    rules = readability_policy()
    signals = metrics.get('readability') or {}
    values = {key: metrics.get(key) for key in ('maxNestingDepth', 'statementCount', 'branchCount')}
    values.update({key: signals.get(key) for key in ('maxBooleanOperands', 'mixedBooleanExpressions')})
    known = (metrics.get('status') == 'complete' and signals.get('status') == 'complete' and
             all(type(value) in (int, float) and isfinite(value) and value >= 0 for value in values.values()))
    penalties = []
    if known:
        for name, rule in rules['penalties'].items():
            observed = values[rule['metric']]
            penalty = min(rule['cap'], max(0, observed - rule['free']) * rule['rate'])
            penalties.append({'rule': name, 'metric': rule['metric'], 'observed': observed,
                              'free': rule['free'], 'penalty': round(penalty, 2)})
    by_rule = {item['rule']: item['penalty'] for item in penalties}
    total = None
    if known:
        control = max(by_rule['nesting'], by_rule['decisions'])
        expressions = max(by_rule['booleanOperands'], by_rule['mixedBooleans'])
        total = control + by_rule['size'] + expressions
        control_rule = 'nesting' if by_rule['nesting'] >= by_rule['decisions'] else 'decisions'
        expression_rule = 'booleanOperands' if by_rule['booleanOperands'] >= by_rule['mixedBooleans'] else 'mixedBooleans'
        for item in penalties:
            item['appliedPenalty'] = item['penalty'] if item['rule'] in {'size', control_rule, expression_rule} else 0
    requires_review = any(item.get('severity') in {'warning', 'critical'} for item in findings)
    verdict = 'UNKNOWN'
    if requires_review or total:
        verdict = 'REVIEW'
    elif known:
        verdict = 'NOT_REVIEWED'
    return {'version': rules['version'], 'status': 'complete' if known else 'unavailable',
            'score': round(max(0, 100 - total), 1) if known else None,
            'verdict': verdict, 'penalties': penalties, 'formula': rules['formula'],
            'calibration': rules['calibration'], 'humanReview': 'not-performed',
            'unverifiedDimensions': list(rules['unverifiedDimensions']),
            'halstead': metrics.get('halstead'),
            'sourceKind': metrics.get('sourceKind', 'unavailable'), 'sourceHash': metrics.get('sourceHash'),
            'scope': 'local syntax only; deferred bodies and called methods reviewed separately',
            'interpretation': rules['description']}
