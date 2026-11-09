"""Explainable file/folder heuristics, separate from collecting static topology."""

from .code_health_policy import finding, policy, weighted_score

VERSION = 'code-organization-v2'


def score_organization(filename: str, metrics: dict, folder: dict, complete: bool, *, coverage: dict | None = None) -> dict:
    """Score only complete selected-scope facts; known problems remain visible in partial data."""
    rules = policy()['organization']
    penalty, findings = 0, []
    checks = [
        ('large-file', metrics['statements'], rules['statementsFree'], rules['statementPenalty'], 40),
        ('many-file-declarations', metrics['declarations'], rules['declarationsFree'], rules['declarationPenalty'], 20),
        ('cross-file-coupling', metrics['outgoingFiles'], rules['outgoingFilesFree'], 3, 20),
        ('crowded-folder', folder['files'], rules['folderFilesFree'], 1, 20),
        ('cross-folder-coupling', folder['outgoingFolders'], rules['outgoingFoldersFree'], 4, 20),
    ]
    for rule, value, free, rate, cap in checks:
        if value > free:
            penalty += min(cap, (value - free) * rate)
            findings.append(finding(rule, file=filename, evidence={'observed': value, 'free': free}))
    if metrics['inCycle'] or folder['inCycle']:
        penalty += rules['cyclePenalty']
        findings.append(finding('organization-cycle', file=filename,
                                evidence={'fileCycle': metrics['inCycle'], 'folderCycle': folder['inCycle']}))
    # Tiny independent DTOs/configurations are not penalized. A connected set of
    # tiny files is a review signal, not proof that the decomposition is wrong.
    if metrics['connectedTinyFiles'] >= rules['tinyFilesFree']:
        penalty += rules['fragmentationPenalty']
        findings.append(finding('file-fragmentation', file=filename,
                                evidence={'connectedTinyFiles': metrics['connectedTinyFiles']}))
    return {'version': VERSION, 'status': 'complete' if complete else 'partial',
            'score': round(max(0, 100 - penalty), 1) if complete else None,
            'coverage': coverage or {},
            'file': filename, 'folder': folder['path'], 'metrics': metrics,
            'folderMetrics': {key: value for key, value in folder.items() if key != 'path'},
            'findings': findings, 'scope': 'selected-files; folder counts are direct children within this scope',
            'interpretation': 'Static size and coupling signals; semantic cohesion, naming and architecture intent are not measured.'}


def transparency_score(components: dict) -> float | None:
    """Combine flow, organization, documentation and dependency clarity; never fill unknowns with zero."""
    weights = policy()['transparencyWeights']
    if any(components.get(key) is None for key in weights):
        return None
    return weighted_score(components, weights)
