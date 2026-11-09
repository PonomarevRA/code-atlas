"""Bounded public analysis outcomes, separate from executable availability and graph facts."""
import re

MAX_REASONS = 8
MAX_REASON_CHARS = 96


def analysis_outcome():
    """
    Create an empty detached publication outcome for one provider/target scope.

    Returns raw provider fact counts, accepted/rejected source bindings, confirmed
    edge contributions, selected query count and bounded machine-readable reasons.
    Zero projected counts mean that no new facts have been published by this run.
    """
    return {'rawFacts': 0, 'projectedFacts': 0, 'rejectedFacts': 0,
            'confirmedEdges': 0, 'queryCount': 0, 'reasons': []}


def add_reasons(outcome, reasons, *, priority=False):
    """
    Append unique bounded machine codes without exposing arbitrary engine prose.

    outcome is the mutable public scope dictionary; reasons is an iterable of
    provider or host codes. Unknown/free-form values become one omission marker.
    priority defaults to false; true places essential host classification before
    provider prose so a full diagnostic list cannot hide why nothing was applied.
    Order within each group is preserved. Mutates outcome and returns no value.
    """
    normalized = []
    for reason in reasons:
        if reason is None or reason == '':
            continue
        if (not isinstance(reason, str) or len(reason) > MAX_REASON_CHARS
                or not re.fullmatch(r'[a-z][A-Za-z0-9_.:-]*', reason)):
            reason = 'provider-diagnostics-omitted'
        if reason not in normalized:
            normalized.append(reason)
        if len(normalized) == MAX_REASONS:
            break
    ordered = [*outcome['reasons'], *normalized]
    if priority:
        ordered = [*normalized, *outcome['reasons']]
    outcome['reasons'] = []
    for reason in ordered:
        if reason not in outcome['reasons']:
            outcome['reasons'].append(reason)
        if len(outcome['reasons']) == MAX_REASONS:
            break


def record_result(outcome, result):
    """
    Retain typed callback status and safe diagnostics before projection succeeds.

    outcome is a mutable scope outcome. result is a validated SemanticResult;
    its raw count is bounded by that contract, while projected counts stay zero.
    Mutates the outcome and returns no value; it does not claim publication.
    """
    outcome['resultStatus'] = result.status
    outcome['rawFacts'] = len(result.facts)
    add_reasons(outcome, result.diagnostics)
    for coverage in result.coverage.values():
        add_reasons(outcome, coverage.reasons)
    if result.status == 'unsupported':
        add_reasons(outcome, ['analysis-unsupported'], priority=True)
    elif result.status in {'failed', 'cancelled'}:
        add_reasons(outcome, ['analysis-' + result.status], priority=True)
    elif not result.facts:
        add_reasons(outcome, ['no-provider-facts'], priority=True)


def apply_publication(providers, projection_counts):
    """
    Commit accepted counts and applied phases after the overlay write succeeds.

    providers is the current bounded list of public provider/target records.
    projection_counts maps exact (provider ID, target ID) tuples to counts produced
    by make_overlay's binding pass. Mutates records and returns no value. Accepted
    candidate/unknown facts count even when no confirmed edge was constructed.
    """
    for provider in providers:
        counts = projection_counts.get((provider['id'], provider['targetId']))
        if counts is None:
            continue
        outcome = provider['outcome']
        outcome.update(counts)
        classification = []
        if outcome['rejectedFacts']:
            classification.append('source-bindings-rejected')
        if outcome['projectedFacts']:
            provider['enrichment'] = 'applied'
        else:
            provider['enrichment'] = 'partial'
            classification.append('no-source-bindings')
        add_reasons(outcome, classification, priority=True)
