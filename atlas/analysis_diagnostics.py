"""Diagnostic facts are separate from program declarations and executable relationships."""

from copy import deepcopy


def diagnostic_summary(graph: dict) -> dict:
    """
    Select constant-size completeness counters before node/detail response budgeting.

    Args:
        graph: Published snapshot containing optional analysisDiagnostics metadata.
    Returns:
        Top-level analysisStatus/errorCount/coverage, treating legacy coverage as unknown.
    """
    diagnostics = graph.get('metadata', {}).get('analysisDiagnostics') or {}
    coverage = {key: diagnostics[key] for key in
                ('sourceFileCount', 'processedFileCount', 'failedFileCount', 'excludedDataCount', 'truncated')
                if key in diagnostics}
    return {'analysisStatus': diagnostics.get('analysisStatus', 'unknown'),
            'errorCount': diagnostics.get('errorCount', 0), 'coverage': coverage,
            **{key: diagnostics.get(key) for key in ('sourceFileCount', 'processedFileCount',
                                                    'failedFileCount', 'excludedDataCount')}}


def attach_diagnostics(graph: dict, diagnostics: dict) -> None:
    """
    Attach bounded diagnostic file nodes without inventing declarations or code edges.

    Args:
        graph: Newly normalized graph owned by the build; mutated in place.
        diagnostics: Standalone worker's bounded, safe issue report.
    Returns:
        None. Identifier collisions fail publication rather than overwrite provider facts.
    Raises:
        ValueError: Diagnostic namespace collides with an existing provider ID.
    """
    graph.setdefault('metadata', {})['analysisDiagnostics'] = deepcopy(diagnostics)
    ids = {node['id'] for node in graph['nodes']}
    for issue in diagnostics.get('issues', []):
        identifier = issue['nodeId']
        if identifier in ids:
            raise ValueError('Analysis diagnostic identifier collision')
        ids.add(identifier)
        graph['nodes'].append({'id': identifier, 'kind': 'analysis-error',
            'label': issue['path'], 'file': issue['path'], 'line': None,
            'description': issue['message'], 'analysisIssue': deepcopy(issue),
            'sourceKind': 'analysis-diagnostic', 'runtimeStatus': 'not-applicable'})
    graph['metadata']['diagnosticNodeCount'] = len(diagnostics.get('issues', []))
    graph['metadata']['programNodeCount'] = len(graph['nodes']) - len(diagnostics.get('issues', []))
    graph['metadata']['nodeCount'] = len(graph['nodes'])
