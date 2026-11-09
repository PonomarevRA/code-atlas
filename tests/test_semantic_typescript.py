"""Real local TS5 worker qualification plus bounded, stale and unavailable cases."""
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Event

import pytest

from atlas.mod_analysis import active_analysis_rules, active_semantic_rules
from atlas.mod_store import ModStore
from atlas.semantic_models import SemanticContext
from atlas.semantic_process import SemanticProcessError
from atlas.semantic_providers.typescript import TypeScriptProvider

HASH = 'a' * 64
LOCAL_TEST_ENGINE = '/usr/local/lib/node_modules/n8n/node_modules/typescript/lib/typescript.js'


def utf16(value):
    """Measure columns in the compiler's UTF-16 code units, including astral Unicode."""
    return len(value.encode('utf-16-le')) // 2


def request_for(tmp_path, *, dynamic=False, alias=False, unicode=False, language='typescript'):
    """Create a pinned two-source call request through the same provider protocol as production."""
    suffix = '.ts' if language == 'typescript' else '.js'
    imported = '@source/helper' if alias else './helper'
    parameter = 'helper: any' if dynamic else ''
    prefix = 'const greeting="🌍"; ' if unicode else ''
    caller = f'import {{ run as helper }} from "{imported}";\nexport function main({parameter}) {{ {prefix}return helper(1); }}\n'
    helper = 'export function run(value: number): number { return value + 1; }\n' if language == 'typescript' else 'export function run(value) { return value + 1; }\n'
    texts = {'main' + suffix: caller, 'helper' + suffix: helper}
    files = []
    for name, text in texts.items():
        (tmp_path / name).write_text(text)
        files.append({'path': name, 'text': text, 'sourceHash': sha256(text.encode()).hexdigest()})
    name = 'main' + suffix
    line = caller.splitlines()[1]
    start = line.index('helper(1)')
    span = {'path': name, 'start_line': 1, 'start_column': line.index('function'), 'end_line': 1,
            'end_column': utf16(line), 'coordinate_kind': 'utf-16'}
    site = {**span, 'start_column': utf16(line[:start]), 'end_column': utf16(line[:start + len('helper(1)')])}
    query = {'sourceNodeId': 'main', 'path': name, 'line': 1, 'column': site['start_column'],
             'endLine': 1, 'endColumn': site['end_column'], 'kind': 'call', 'label': 'main',
             'sourceSpan': span, 'callsite': site}
    if alias:
        (tmp_path / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {
            'baseUrl': '.', 'paths': {'@source/*': ['./*']}}}))
    context = SemanticContext('snapshot', 'workspace', str(tmp_path), 'typescript', HASH,
                               {item['path']: item['sourceHash'] for item in files}, HASH)
    return context, {'files': files, 'queries': [query]}


@pytest.fixture
def engine():
    """Use an already installed engine only; never install a test or production dependency."""
    path = Path(os.environ.get('ATLAS_TEST_TYPESCRIPT_PATH', LOCAL_TEST_ENGINE))
    if not path.is_file():
        pytest.skip('No existing TypeScript engine for real-provider qualification')
    return path


def test_real_typescript_cross_file_static_binding_and_unicode(tmp_path, engine):
    """Installed Compiler API resolves a real alias to its exact cross-file declaration span."""
    context, request = request_for(tmp_path, unicode=True)
    provider = TypeScriptProvider('semantic-typescript/typescript', engine_path=engine)
    probe = provider.probe(context, Event())
    assert probe.availability.status == 'available', probe
    result = provider.analyze(context, probe, request, Event())
    fact = result.facts[0]
    assert fact.confidence == 'candidate'
    assert fact.target.source_span.path == 'helper.ts'
    assert fact.target.label == 'run'
    assert fact.source.graph_node_id == 'main'
    assert fact.callsite.start_column == request['queries'][0]['callsite']['start_column']
    assert result.status == 'partial' and all(item.total is None for item in result.coverage.values())
    assert 'ambient-types-and-standard-library-not-loaded' in result.diagnostics


def test_real_typescript_any_shadow_does_not_bind_imported_target(tmp_path, engine):
    """A same-named any parameter cannot acquire the imported function's confirmed edge."""
    context, request = request_for(tmp_path, dynamic=True)
    provider = TypeScriptProvider('typescript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    assert result.facts[0].confidence == 'unknown'
    assert result.facts[0].target is None


def test_real_typescript_config_paths_and_partial_project_references(tmp_path, engine):
    """Local JSON aliases work, while unbuilt reference/extends contexts remain explicit partial."""
    context, request = request_for(tmp_path, alias=True)
    path = tmp_path / 'tsconfig.json'
    config = json.loads(path.read_text())
    config.update(extends='./not-loaded.json', references=[{'path': './other'}])
    path.write_text(json.dumps(config))
    provider = TypeScriptProvider('typescript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    assert result.facts[0].confidence == 'candidate'
    assert result.facts[0].target.source_span.path == 'helper.ts'
    assert {'configuration-extends-not-loaded', 'project-references-not-loaded'} <= set(result.diagnostics)


def test_stale_source_query_scope_and_cancel_fail_closed(tmp_path, engine):
    """The adapter checks text/hash/source spans before accepting any worker result."""
    context, request = request_for(tmp_path)
    provider = TypeScriptProvider('typescript', engine_path=engine)
    probe = provider.probe(context, Event())
    request['files'][0]['text'] += '// changed'
    with pytest.raises(ValueError, match='stale'):
        provider.analyze(context, probe, request, Event())
    context, request = request_for(tmp_path)
    request['queries'][0]['callsite']['path'] = 'helper.ts'
    with pytest.raises(ValueError, match='Mismatched'):
        provider.analyze(context, probe, request, Event())
    context, request = request_for(tmp_path)
    cancellation = Event()
    cancellation.set()
    with pytest.raises(SemanticProcessError, match='cancelled'):
        provider.analyze(context, probe, request, cancellation)


def test_missing_and_incompatible_engine_do_not_claim_applied(tmp_path):
    """An explicit missing engine never falls back; API-major mismatches are not ready."""
    context, _ = request_for(tmp_path)
    missing = TypeScriptProvider('typescript', engine_path=tmp_path / 'missing.js')
    assert missing.probe(context, Event()).availability.status == 'missing'
    incompatible = tmp_path / 'engine.js'
    incompatible.write_text('module.exports={version:"7.0.0"};')
    provider = TypeScriptProvider('typescript', engine_path=incompatible)
    assert provider.probe(context, Event()).availability.status == 'incompatible'


def test_semantic_typescript_mod_off_preserves_baseline(tmp_path):
    """The actual bundled profile claims semantics separately from project rule ownership."""
    bundled = Path(__file__).resolve().parents[1] / 'mods'
    store = ModStore(tmp_path, bundled)
    inventory = {'files': ['main.ts'], 'truncated': False, 'mode': 'all', 'version': 'test', 'warnings': []}
    baseline = active_analysis_rules(store, inventory=inventory)
    rules = active_semantic_rules(store, inventory=inventory)
    provider = next(item for item in rules['semanticProviders'] if item['language'] == 'typescript')
    assert provider['strategy'] == 'typescript-local-v1'
    item = next(item for item in store.catalog()['mods'] if item['id'] == 'semantic-typescript')
    store.enable(item['id'], item['fingerprint'], False)
    assert not active_semantic_rules(store, inventory=inventory)['semanticProviders']
    assert active_analysis_rules(store, inventory=inventory) == baseline


def test_real_typescript_result_reaches_production_effective_graph(tmp_path, engine):
    """Real compiler candidates preserve exact target identity without manufacturing confirmed edges."""
    from atlas.semantic_sources import declaration_bindings
    from atlas.semantic_overlay import make_overlay
    context, request = request_for(tmp_path)
    provider = TypeScriptProvider('semantic-typescript/typescript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    graph = {'nodes': [
        {'id': 'main', 'file': 'main.ts', 'line': 2, 'kind': 'method', 'label': 'main()'},
        {'id': 'run', 'file': 'helper.ts', 'line': 1, 'kind': 'method', 'label': 'run()'}], 'edges': []}
    snapshot = tmp_path / 'maps' / 'snapshot' / 'atlas-graph.json'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(graph))
    contents, bindings = declaration_bindings(tmp_path, graph, context.source_hashes,
                                               context.snapshot_id, context.source_hashes)
    rules = {'semanticProviders': [{'modId': 'semantic-typescript', 'id': 'typescript',
                                    'modFingerprint': HASH}], 'fingerprint': HASH}
    delta = make_overlay(snapshot, graph, tmp_path, [result], bindings, contents, rules)
    assert delta['edges'] == []
    stored = delta['facts']['main'][0]
    assert stored['confidence'] == 'candidate' and stored['target']['graph_node_id'] == 'run'
    assert stored['runtimeSelectionKnown'] is False
    assert graph['edges'] == []


def test_timeout_status_is_reported_without_applied_facts(tmp_path, engine, monkeypatch):
    """A bounded-transport timeout becomes a failed discovery, not an empty successful result."""
    from atlas.semantic_providers import typescript
    context, _ = request_for(tmp_path)

    def timeout(*args, **kwargs):
        """Inject the shared transport's stable timeout outcome."""
        raise SemanticProcessError('timeout')

    monkeypatch.setattr(typescript, 'run_bounded', timeout)
    state = TypeScriptProvider('typescript', engine_path=engine).probe(context, Event())
    assert state.availability.status == 'failed' and state.availability.reason == 'timeout'


def test_real_typescript_overload_error_remains_uncertain(tmp_path, engine):
    """Compiler recovery cannot be promoted to a confirmed static call."""
    context, request = request_for(tmp_path)
    text = request['files'][1]['text'].replace('value: number', 'value: string')
    request['files'][1].update(text=text, sourceHash=sha256(text.encode()).hexdigest())
    context.source_hashes['helper.ts'] = request['files'][1]['sourceHash']
    provider = TypeScriptProvider('typescript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    assert result.facts[0].confidence == 'candidate'


def test_one_shot_backend_leaves_no_owned_compiler_process(tmp_path, engine):
    """A completed real request leaves no compiler process retained by the Python adapter."""
    import psutil
    context, request = request_for(tmp_path)
    provider = TypeScriptProvider('typescript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    assert result.facts
    assert not any('typescript_worker.cjs' in ' '.join(child.cmdline())
                   for child in psutil.Process().children(recursive=True))


def test_bom_and_unqualified_scope_reasons_are_preserved(tmp_path, engine):
    """The safe reader's decoded BOM text retains exact original-byte and scope provenance."""
    context, request = request_for(tmp_path)
    first = request['files'][0]
    digest = sha256(b'\xef\xbb\xbf' + first['text'].encode()).hexdigest()
    first['sourceHash'] = digest
    context.source_hashes[first['path']] = digest
    request['scope'] = {'reasons': ['compilation-target-unqualified']}
    provider = TypeScriptProvider('typescript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    assert result.facts[0].confidence == 'candidate'
    assert 'compilation-target-unqualified' in result.diagnostics


@pytest.mark.parametrize('definition', [
    'export const run = (value: number) => value + 1;\n',
    'export const run = function(value: number) { return value + 1; };\n',
])
def test_bound_callable_identifier_reaches_production_graph(tmp_path, engine, definition):
    """Arrow/function candidates identify their bound variable and do not add confirmed call edges."""
    from atlas.semantic_sources import declaration_bindings
    from atlas.semantic_overlay import make_overlay
    context, request = request_for(tmp_path)
    request['files'][1].update(text=definition, sourceHash=sha256(definition.encode()).hexdigest())
    context.source_hashes['helper.ts'] = request['files'][1]['sourceHash']
    (tmp_path / 'helper.ts').write_text(definition)
    provider = TypeScriptProvider('semantic-typescript/typescript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    target = result.facts[0].target
    assert target.kind == 'function' and target.label == 'run'
    graph = {'nodes': [
        {'id': 'main', 'file': 'main.ts', 'line': 2, 'kind': 'method', 'label': 'main()'},
        {'id': 'run', 'file': 'helper.ts', 'line': 1, 'kind': 'method', 'label': 'run()'}], 'edges': []}
    snapshot = tmp_path / 'maps' / 'snapshot' / 'atlas-graph.json'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(graph))
    contents, bindings = declaration_bindings(tmp_path, graph, context.source_hashes,
                                               context.snapshot_id, context.source_hashes)
    rules = {'semanticProviders': [{'modId': 'semantic-typescript', 'id': 'typescript',
                                    'modFingerprint': HASH}], 'fingerprint': HASH}
    delta = make_overlay(snapshot, graph, tmp_path, [result], bindings, contents, rules)
    assert delta['edges'] == []
    assert delta['facts']['main'][0]['target']['graph_node_id'] == 'run'
    assert delta['facts']['main'][0]['confidence'] == 'candidate'


def test_real_missing_ambient_overload_changes_target_without_confirming(tmp_path, engine):
    """An uncaptured ambient overload can replace a valid binding; subset results remain candidates."""
    caller = 'function main() { return choose(1); }\n'
    texts = {'main.ts': caller, 'base.d.ts': 'declare function choose(value: number): number;\n'}
    extra = 'declare function choose(value: 1): "special";\n'
    (tmp_path / 'extra.d.ts').write_text(extra)
    files = [{'path': path, 'text': text, 'sourceHash': sha256(text.encode()).hexdigest()}
             for path, text in texts.items()]
    source = {'path': 'main.ts', 'start_line': 0, 'start_column': 0, 'end_line': 0,
              'end_column': len(caller.rstrip()), 'coordinate_kind': 'utf-16'}
    site = {**source, 'start_column': caller.index('choose'), 'end_column': caller.index('choose') + 9}
    query = {'sourceNodeId': 'main', 'path': 'main.ts', 'line': 0, 'column': site['start_column'],
             'kind': 'call', 'label': 'main', 'sourceSpan': source, 'callsite': site}
    context = SemanticContext('snapshot', 'workspace', str(tmp_path), 'typescript', HASH,
                               {item['path']: item['sourceHash'] for item in files}, HASH)
    provider = TypeScriptProvider('typescript', engine_path=engine)
    probe = provider.probe(context, Event())
    request = {'files': files, 'queries': [query], 'sourceClosureProven': True,
               'scope': {'qualified': True, 'sourceClosureProven': True}}
    subset = provider.analyze(context, probe, request, Event())
    assert subset.facts[0].target.source_span.path == 'base.d.ts'
    assert subset.facts[0].confidence == 'candidate'
    files.append({'path': 'extra.d.ts', 'text': extra, 'sourceHash': sha256(extra.encode()).hexdigest()})
    context.source_hashes['extra.d.ts'] = files[-1]['sourceHash']
    expanded = provider.analyze(context, probe, request, Event())
    assert expanded.facts[0].target.source_span.path == 'extra.d.ts'
    assert expanded.facts[0].confidence == 'candidate'
    assert 'complete-project-reference-closure-unproven' in subset.diagnostics


def test_real_diagnostic_outside_call_is_not_ignored(tmp_path, engine):
    """A helper-body error matters even when the caller expression has no overlapping diagnostic."""
    context, request = request_for(tmp_path)
    text = 'export function run(value: number): number { return "wrong type"; }\n'
    request['files'][1].update(text=text, sourceHash=sha256(text.encode()).hexdigest())
    context.source_hashes['helper.ts'] = request['files'][1]['sourceHash']
    provider = TypeScriptProvider('typescript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    assert result.facts[0].confidence == 'candidate'
    assert 'compiler-semantic-diagnostics-in-context' in result.diagnostics
    assert result.facts[0].evidence == 'complete-project-reference-closure-unproven'
