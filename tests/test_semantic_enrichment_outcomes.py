"""Truthful selected-scope outcomes and normal-use invalidation without real engine processes."""
from dataclasses import replace
import json
import os

import pytest

from atlas import semantic_enrichment, store
from atlas.semantic_enrichment import SemanticEnrichment
from atlas.semantic_models import SemanticAvailability, SemanticCoverage, SourceSpan
from atlas.semantic_outcomes import add_reasons, analysis_outcome, apply_publication, record_result, MAX_REASONS
from atlas.semantic_overlay import make_overlay
from atlas.semantic_runtime import ProbeOutcome
from test_semantic_overlay import semantic_project


def drain(service):
    """
    Wait for submitted coordinator jobs in the test, without polling or scheduling probes.

    service is the active fixture coordinator. Returns no value; raises on timeout.
    """
    service.executor.submit(lambda: None).result(timeout=5)


@pytest.fixture
def outcome_service(semantic_project):
    """
    Prepare one source-qualified graph and a configurable process-free provider factory.

    semantic_project supplies the actual store/AST/overlay fixture. Returns a factory
    whose mode selects typed callback behavior; all created coordinators close on exit.
    """
    state, identifier, path, root, graph, evidence, _, _, _ = semantic_project
    for node in graph['nodes']:
        node['codeProjectId'] = 'python-project'
    store.write_json(path, graph)
    engine = state.data_dir / 'fixture-engine'
    engine.write_text('fixture-engine')
    services = []

    def create(mode='bound'):
        """
        Create a coordinator for mode and return it with captured requests and store inputs.

        mode is bound/candidate/unknown/unmapped/mixed/unsupported/empty/failed.
        Results exercise real projection/proof fencing; no engine process is launched.
        """
        requests, probes = [], []

        class Provider:
            """Return typed fixture evidence with the actual runtime's context/provenance."""
            def probe(self, context, cancel):
                """
                Capture context; cancel is the unused cooperative token for this instant probe.

                Returns available with one real fixture asset for provenance validation.
                """
                probes.append(context)
                return ProbeOutcome(SemanticAvailability('available', engine_fingerprint='b' * 64), (engine,))

            def analyze(self, context, probe, request, cancel):
                """
                Return mode-selected evidence anchored to context and its fixture probe.

                request is the actual captured-source payload; cancel is unused by this
                instant callback. Returns SemanticResult or raises RuntimeError in failed mode.
                """
                requests.append(request)
                if mode == 'failed':
                    raise RuntimeError('fixture analysis failure')
                fact = evidence.facts[0]
                if mode == 'candidate':
                    fact = replace(fact, confidence='candidate')
                elif mode == 'unknown':
                    fact = replace(fact, confidence='unknown', target=None)
                unbound = replace(fact, source=replace(fact.source, source_span=SourceSpan('a.py', 0, 0, 0, 4)))
                facts = (unbound,) if mode == 'unmapped' else (fact, unbound) if mode == 'mixed' else (fact,)
                if mode in {'unsupported', 'empty'}:
                    facts = ()
                status = 'unsupported' if mode == 'unsupported' else 'partial'
                coverage = {'definition': SemanticCoverage('unsupported' if mode == 'unsupported' else 'partial')}
                return replace(evidence, context=context, facts=facts, status=status, coverage=coverage,
                               diagnostics=('fixture-unsupported-context',) if mode == 'unsupported' else ())

        service = SemanticEnrichment(state, provider_factory=lambda declaration, paths: Provider())
        services.append(service)
        return service, state, identifier, path, root, graph, requests, probes

    yield create
    for service in services:
        service.close()


@pytest.mark.parametrize('mode,raw,projected,rejected,edges,phase', [
    ('bound', 1, 1, 0, 1, 'applied'),
    ('candidate', 1, 1, 0, 0, 'applied'),
    ('unknown', 1, 1, 0, 0, 'applied'),
    ('unmapped', 1, 0, 1, 0, 'partial'),
    ('mixed', 2, 1, 1, 1, 'applied'),
    ('empty', 0, 0, 0, 0, 'partial'),
    ('unsupported', 0, 0, 0, 0, 'partial'),
])
def test_phase_counts_published_source_facts(outcome_service, mode, raw, projected, rejected, edges, phase):
    """
    Compare status counts to the actually published overlay, including rejected and candidate facts.

    outcome_service creates typed callbacks. mode and raw/projected/rejected/edges/phase
    specify exact expected counts and public phase; candidate/unknown facts need no edge.
    """
    service, state, identifier, path, _, _, requests, _ = outcome_service(mode)
    original = path.read_bytes()
    service.use(identifier, path.parent.name, ['run'])
    drain(service)
    public = service.status(identifier, path.parent.name)
    provider = public['providers'][0]
    outcome = provider['outcome']
    assert provider['availability']['status'] == 'available'
    assert public['enrichment'] == provider['enrichment'] == phase
    assert outcome['rawFacts'] == raw
    assert outcome['projectedFacts'] == projected
    assert outcome['rejectedFacts'] == rejected
    assert outcome['confirmedEdges'] == edges
    assert outcome['queryCount'] == len(requests[0]['queries']) == 1
    if mode == 'unsupported':
        assert outcome['resultStatus'] == 'unsupported'
        assert 'analysis-unsupported' in outcome['reasons']
        assert 'fixture-unsupported-context' in outcome['reasons']
        assert not path.with_name('semantic-overlay.json').exists()
    else:
        delta = json.loads(path.with_name('semantic-overlay.json').read_text())
        assert sum(map(len, delta['facts'].values())) == projected
        assert len(delta['edges']) == edges
    if rejected:
        assert 'source-bindings-rejected' in outcome['reasons']
    if mode == 'unmapped':
        assert 'no-source-bindings' in outcome['reasons']
    assert path.read_bytes() == original
    assert public['runtimeSelectionKnown'] is False
    assert len(state.get_graph(identifier)['edges']) == edges


def test_no_queries_does_not_call_available_engine(outcome_service):
    """
    A call-free selected declaration reports no-queries while keeping discovery available.

    outcome_service supplies the graph containing work(), whose body has no calls.
    """
    service, _, identifier, path, _, _, requests, probes = outcome_service()
    service.use(identifier, path.parent.name, ['work'])
    drain(service)
    provider = service.status(identifier, path.parent.name)['providers'][0]
    assert probes and requests == []
    assert provider['availability']['status'] == 'available'
    assert provider['enrichment'] == 'partial'
    assert provider['outcome']['queryCount'] == 0
    assert 'no-queries' in provider['outcome']['reasons']
    assert not path.with_name('semantic-overlay.json').exists()


def test_engine_failure_retains_current_availability_and_reason(outcome_service):
    """
    A failing callback reports engine failure rather than source binding or unsupported analysis.

    outcome_service supplies the throwing fake adapter and real runtime error boundary.
    """
    service, _, identifier, path, _, _, _, _ = outcome_service('failed')
    service.use(identifier, path.parent.name, ['run'])
    drain(service)
    provider = service.status(identifier, path.parent.name)['providers'][0]
    assert provider['availability']['status'] == 'failed'
    assert provider['enrichment'] == 'failed'
    assert 'engine-analysis-no-result' in provider['outcome']['reasons']
    assert 'analysis-failed:RuntimeError' in provider['outcome']['reasons']
    assert provider['outcome']['projectedFacts'] == 0
    assert not path.with_name('semantic-overlay.json').exists()


def test_failed_write_cannot_report_applied(outcome_service, monkeypatch):
    """
    Failure after construction leaves public accepted counts zero and baseline graph intact.

    outcome_service supplies valid evidence; monkeypatch injects an overlay publication error.
    """
    service, state, identifier, path, _, _, _, _ = outcome_service()
    def fail_write(snapshot, overlay):
        """Reject snapshot/overlay publication at the actual final boundary with an OSError."""
        raise OSError('fixture publication denied')
    monkeypatch.setattr(semantic_enrichment, 'write_overlay', fail_write)
    service.use(identifier, path.parent.name, ['run'])
    drain(service)
    public = service.status(identifier, path.parent.name)
    provider = public['providers'][0]
    assert public['enrichment'] == provider['enrichment'] == 'failed'
    assert provider['outcome']['rawFacts'] == 1
    assert provider['outcome']['projectedFacts'] == 0
    assert 'analysis-publication-failed' in provider['outcome']['reasons']
    assert not path.with_name('semantic-overlay.json').exists()
    assert state.get_graph(identifier)['edges'] == []


def test_ambiguous_source_alias_cannot_report_applied(outcome_service):
    """
    Equal-span graph aliases reject the raw source fact without claiming applied.

    outcome_service supplies valid source facts; a second run declaration with the
    same AST span creates the actual ambiguous-binding regression path.
    """
    service, _, identifier, path, _, graph, _, _ = outcome_service()
    graph['nodes'].append({**graph['nodes'][0], 'id': 'same-span-alias'})
    store.write_json(path, graph)
    service.use(identifier, path.parent.name, ['run'])
    drain(service)
    provider = service.status(identifier, path.parent.name)['providers'][0]
    assert provider['enrichment'] == 'partial'
    assert provider['outcome']['rawFacts'] == provider['outcome']['rejectedFacts'] == 1
    assert provider['outcome']['projectedFacts'] == 0
    delta = json.loads(path.with_name('semantic-overlay.json').read_text())
    assert delta['facts'] == {} and delta['edges'] == []


def test_stale_source_has_no_bindings_without_analyzer_call(outcome_service):
    """
    Changed source bytes remain a scope failure while the installed engine stays available.

    outcome_service supplies pinned graph hashes; both captured files change before
    use, so no verified file reaches the analyzer and no query is fabricated.
    """
    service, _, identifier, path, root, _, requests, _ = outcome_service()
    (root / 'a.py').write_text('changed source A')
    (root / 'b.py').write_text('changed source B')
    service.use(identifier, path.parent.name, ['run'])
    drain(service)
    provider = service.status(identifier, path.parent.name)['providers'][0]
    assert requests == [] and provider['availability']['status'] == 'available'
    assert provider['enrichment'] == 'partial'
    assert 'no-source-bindings' in provider['outcome']['reasons']
    assert 'source-unavailable-or-stale' in provider['outcome']['reasons']
    assert not path.with_name('semantic-overlay.json').exists()


@pytest.mark.parametrize('filename', ['rust-project.json', '.classpath'])
def test_required_context_changes_recheck_negative_probe(semantic_project, filename):
    """
    Normal use detects newly created/changed/deleted mandatory context before the daily TTL.

    semantic_project supplies a real coordinator scope; filename names a required Rust/Java
    context file. A context-sensitive fake probe isolates cache invalidation from engine setup.
    """
    state, identifier, path, root, _, _, _, _, _ = semantic_project
    contexts = []
    required = root / filename
    class Provider:
        """Only context existence/bytes matter to this deterministic negative discovery fixture."""
        def probe(self, context, cancel):
            """Capture context and return a missing-context state; cancel is unused, no process runs."""
            contexts.append(context)
            reason = 'fixture-context-present' if required.exists() else 'fixture-context-missing'
            return ProbeOutcome(SemanticAvailability('incompatible', reason))
    service = SemanticEnrichment(state, clock=lambda: 1_800_000_000., provider_factory=lambda declaration, paths: Provider())
    try:
        for content in [None, 'first context', 'changed context bytes', None]:
            if content is None:
                required.unlink(missing_ok=True)
            else:
                required.write_text(content)
            service.use(identifier, path.parent.name)
            drain(service)
            reason = service.status(identifier, path.parent.name)['providers'][0]['availability']['reason']
            assert reason == ('fixture-context-present' if content is not None else 'fixture-context-missing')
        # Deletion can reuse the still-valid original negative observation, but
        # creation and changed bytes must each acquire a distinct discovery key.
        assert len(contexts) == 3
        assert len({context.config_fingerprint for context in contexts}) == 3
    finally:
        service.close()


def test_environment_change_invalidates_coordinator_before_ttl(semantic_project, monkeypatch):
    """
    A discovery environment change resets coordinator and runtime negative identities together.

    semantic_project supplies the scope; monkeypatch changes PATH without launching anything.
    Public status must not expose the changed private path value.
    """
    state, identifier, path, _, _, _, _, _, _ = semantic_project
    contexts = []
    class Provider:
        """Capture private contexts while returning a stable engine-missing result."""
        def probe(self, context, cancel):
            """Record context; cancel is unused by the immediate missing-engine result."""
            contexts.append(context)
            return ProbeOutcome(SemanticAvailability('missing', 'fixture-engine-missing'))
    service = SemanticEnrichment(state, clock=lambda: 1_800_000_000., provider_factory=lambda declaration, paths: Provider())
    try:
        service.use(identifier, path.parent.name)
        drain(service)
        monkeypatch.setenv('PATH', '/private-fixture-discovery-choice')
        service.use(identifier, path.parent.name)
        drain(service)
        assert len(contexts) == 2
        assert contexts[0].config_fingerprint != contexts[1].config_fingerprint
        assert '/private-fixture-discovery-choice' not in json.dumps(service.status(identifier, path.parent.name))
    finally:
        service.close()


@pytest.mark.parametrize('filename', ['rust-project.json', '.classpath'])
@pytest.mark.parametrize('remaining_bytes', [0, 8])
def test_required_context_identity_survives_content_budget(semantic_project, monkeypatch, filename, remaining_bytes):
    """
    Late required context changes invalidate discovery even when content cannot be hashed.

    semantic_project supplies the source scope; monkeypatch audits actual coordinator
    reads. filename is the Rust/Java context input. remaining_bytes leaves zero or
    eight bytes after two valid large JSON inputs, less than the late file's size.
    Create/edit/delete/recreate use normal discovery, including an equal-size edit.
    POSIX retains modification time to exercise change-time identity; Windows has
    creation-time semantics instead. No force, engine or over-budget read runs.
    """
    state, identifier, path, root, _, _, _, _, _ = semantic_project
    first_size = 512 * 1024
    second_size = first_size - remaining_bytes
    (root / 'tsconfig.json').write_bytes(b'{}' + b' ' * (first_size - 2))
    (root / 'jsconfig.json').write_bytes(b'{}' + b' ' * (second_size - 2))
    required = root / filename
    contexts, bytes_read, call_bytes = [], [0], []
    read = semantic_enrichment._read_regular_file

    def audited_read(workspace, relative, max_bytes):
        """
        Read workspace/relative under max_bytes, recording only returned content bytes.

        The late required file exceeds the remaining budget and must never be hashed;
        any attempt remains bounded by max_bytes and is rejected by the safe reader.
        """
        assert 0 < max_bytes <= 512 * 1024
        if relative.as_posix() == filename:
            assert max_bytes <= remaining_bytes and remaining_bytes > 0
        content = read(workspace, relative, max_bytes)
        assert relative.as_posix() != filename
        bytes_read[0] += len(content)
        return content

    class Provider:
        """Capture context keys and report file-specific negative discovery observations."""
        def probe(self, context, cancel):
            """
            Return the current required-context reason and capture private context.

            cancel is unused by the instantaneous fixture; no process is launched.
            """
            contexts.append(context)
            marker = required.read_text() if required.exists() else 'missing'
            return ProbeOutcome(SemanticAvailability('incompatible', 'fixture-config-' + marker))

    service = SemanticEnrichment(state, clock=lambda: 1_800_000_000., provider_factory=lambda declaration, paths: Provider())
    configuration = service._configuration

    def audited_configuration(workspace, config_names):
        """
        Return the real fingerprint for workspace/config_names and audit the whole-read budget.

        This wraps only the configuration boundary, so repeated publication checks
        must each remain within1MiB independently of the number of known paths.
        """
        before = bytes_read[0]
        result = configuration(workspace, config_names)
        consumed = bytes_read[0] - before
        assert consumed <= 1024 * 1024
        call_bytes.append(consumed)
        return result

    monkeypatch.setattr(semantic_enrichment, '_read_regular_file', audited_read)
    monkeypatch.setattr(service, '_configuration', audited_configuration)
    try:
        for marker in [None, 'first-context-0123456789', 'other-context-0123456789', None, 'first-context-0123456789']:
            previous = required.stat() if required.exists() else None
            if marker is None:
                required.unlink(missing_ok=True)
            else:
                required.write_text(marker)
                if previous is not None:
                    assert previous.st_size == required.stat().st_size
                    if os.name != 'nt':
                        os.utime(required, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            service.use(identifier, path.parent.name)
            drain(service)
            expected = 'fixture-config-' + (marker or 'missing')
            assert service.status(identifier, path.parent.name)['providers'][0]['availability']['reason'] == expected
        # Equivalent absent context can reuse its original correct negative result.
        assert len(contexts) == len({context.config_fingerprint for context in contexts}) == 4
        assert call_bytes and set(call_bytes) == {1024 * 1024 - remaining_bytes}
    finally:
        service.close()


def test_projection_audit_counts_provider_targets_separately(semantic_project):
    """
    The same provider's two targets retain separate accepted/rejected publication counters.

    semantic_project supplies exact bindings. Two callback contexts exercise the optional
    audit without changing the persisted overlay schema or inferring runtime dispatch.
    """
    _, _, path, root, graph, result, rules, contents, bindings = semantic_project
    first = replace(result, context=replace(result.context, target_id='python:first'))
    unbound = replace(result.facts[0], source=replace(result.facts[0].source, source_span=SourceSpan('a.py', 0, 0, 0, 4)))
    second = replace(result, context=replace(result.context, target_id='python:second'), facts=(unbound,))
    counts = {}
    make_overlay(path, graph, root, [first, second], bindings, contents, rules, projection_counts=counts)
    providers = [{'id': result.provider_id, 'targetId': item.context.target_id,
                  'enrichment': 'partial', 'outcome': analysis_outcome()} for item in [first, second]]
    apply_publication(providers, counts)
    assert providers[0]['outcome']['projectedFacts'] == 1
    assert providers[0]['enrichment'] == 'applied'
    assert providers[1]['outcome']['projectedFacts'] == 0
    assert providers[1]['outcome']['rejectedFacts'] == 1
    assert providers[1]['enrichment'] == 'partial'


def test_outcome_reasons_are_bounded_machine_codes():
    """Unsafe provider prose/paths never enter the new public outcome reason collection."""
    outcome = analysis_outcome()
    add_reasons(outcome, ['/home/example/tool path', 'x' * 1000, '', None, 'no-queries', 'no-queries'])
    add_reasons(outcome, ['fixture-reason-' + str(index) for index in range(30)])
    assert len(outcome['reasons']) == MAX_REASONS
    assert outcome['reasons'][:2] == ['provider-diagnostics-omitted', 'no-queries']
    assert all(len(reason) <= 96 and '/' not in reason for reason in outcome['reasons'])


def test_essential_classification_survives_full_provider_diagnostics(semantic_project):
    """
    Unsupported and rejected-binding classification cannot be hidden by a full diagnostic list.

    semantic_project supplies a valid typed result; its noisy diagnostics exercise
    the public reason budget without exposing prose or changing availability.
    """
    result = semantic_project[5]
    result = replace(result, status='unsupported', facts=(),
                     diagnostics=tuple('fixture-diagnostic-' + str(index) for index in range(64)))
    outcome = analysis_outcome()
    record_result(outcome, result)
    assert len(outcome['reasons']) == MAX_REASONS
    assert outcome['reasons'][0] == 'analysis-unsupported'
    providers = [{'id': result.provider_id, 'targetId': 'target', 'outcome': outcome, 'enrichment': 'partial'}]
    apply_publication(providers, {(result.provider_id, 'target'): {
        'rawFacts': 1, 'projectedFacts': 0, 'rejectedFacts': 1, 'confirmedEdges': 0}})
    assert outcome['reasons'][:2] == ['source-bindings-rejected', 'no-source-bindings']
    assert len(outcome['reasons']) == MAX_REASONS


def test_projection_audit_preserves_duplicate_fact_counts(semantic_project):
    """
    Repeated facts remain two accepted facts but one unique edge in one provider/target audit.

    semantic_project supplies source-bound evidence. Two callback records for the
    same context exercise count aggregation without collapsing fact multiplicity.
    """
    _, _, path, root, graph, result, rules, contents, bindings = semantic_project
    counts = {}
    delta = make_overlay(path, graph, root, [result, result], bindings, contents, rules,
                         projection_counts=counts)
    outcome = counts[(result.provider_id, result.context.target_id)]
    assert outcome == {'rawFacts': 2, 'projectedFacts': 2, 'rejectedFacts': 0, 'confirmedEdges': 1}
    assert len(delta['facts']['run']) == 2
    assert len(delta['edges']) == 1 and len(delta['edges'][0]['semanticEvidence']) == 2
