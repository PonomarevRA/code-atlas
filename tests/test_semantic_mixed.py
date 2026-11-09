"""Production mixed-map enrichment with installed engines and pinned original-source cohorts.

The normalized map is an explicit fixture, not a full Graphify extraction test.
No analyzer result is mocked and no engine, runtime or dependency is installed.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.mod_analysis import active_analysis_rules
from atlas.semantic_enrichment import SemanticEnrichment
from atlas.semantic_models import SourceSpan
from atlas.semantic_requests import build_semantic_request
from atlas.semantic_sources import span_bytes
from atlas.semantic_provider_registry import create_semantic_provider
from atlas.store import StoreError, write_json
from test_grouping_cache import legacy_store
from test_semantic_typescript import engine

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCES = {
    'dotnet/app/Caller.cs': 'class Caller {\n public static int Run() { return Worker.Work(1); }\n}\n',
    'dotnet/app/Worker.cs': 'class Worker {\n public static int Work(int value) => value;\n}\n',
    'web/ts/main.ts': 'import { Work } from "./helper";\nexport function Run() { return Work(1); }\n',
    'web/ts/helper.ts': 'export function Work(value: number): number { return value; }\n',
    'web/js/main.js': 'import { Work } from "./helper";\nexport function Run() { return Work(1); }\n',
    'web/js/helper.js': '/** @param {number} value */\nexport function Work(value) { return value; }\n',
    'jvm/java/Main.java': 'class Main {\n static int Run() { return Helper.Work(1); }\n}\n',
    'jvm/java/Helper.java': 'class Helper {\n static int Work(int value) { return value; }\n}\n',
    'jvm/kotlin/Main.kt': 'fun Run(): Int {\n return Work(1)\n}\n',
    'jvm/kotlin/Helper.kt': 'fun Work(value: Int): Int { return value }\n',
}
# Equal method names and shared web/JVM owners must not merge compiler contexts.
COHORTS = {
    'csharp': ('dotnet-owner', 'dotnet/app/Caller.cs', 'dotnet/app/Worker.cs'),
    'typescript': ('web-owner', 'web/ts/main.ts', 'web/ts/helper.ts'),
    'javascript': ('web-owner', 'web/js/main.js', 'web/js/helper.js'),
    'java': ('jvm-owner', 'jvm/java/Main.java', 'jvm/java/Helper.java'),
    'kotlin': ('jvm-owner', 'jvm/kotlin/Main.kt', 'jvm/kotlin/Helper.kt'),
}
MODS = {
    'csharp': ('semantic-csharp', 'roslyn-local-v1'),
    'typescript': ('semantic-typescript', 'typescript-local-v1'),
    'javascript': ('semantic-javascript', 'typescript-local-v1'),
    'java': ('semantic-java', 'jdt-local-v1'),
    'kotlin': ('semantic-kotlin', 'kotlin-local-v1'),
}


@pytest.fixture
def mixed_project(tmp_path):
    """Pin ten real declarations and hashes in one immutable normalized snapshot."""
    root = tmp_path / 'subject'
    for filename, text in SOURCES.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    (root / 'dotnet/app/app.csproj').write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework>'
        '</PropertyGroup></Project>')
    (root / 'web/package.json').write_text('{"private":true,"scripts":{"postinstall":"exit 1"}}')
    (root / 'jvm/build.gradle.kts').write_text('error("Atlas must not execute this build")\n')
    state, identifier, snapshot, _ = legacy_store(tmp_path)
    raw = state.raw_graph(identifier)
    hashes = {path: sha256(text.encode()).hexdigest() for path, text in SOURCES.items()}
    nodes = []
    for language, (owner, caller, helper) in COHORTS.items():
        for name, filename in [('Run', caller), ('Work', helper)]:
            line = next(index + 1 for index, text in enumerate(SOURCES[filename].splitlines())
                        if name + '(' in text)
            nodes.append({'id': f'{language}:{name}', 'kind': 'method', 'label': name + '()',
                'file': filename, 'line': line, 'codeProjectId': owner,
                'memberFacts': {'name': name, 'language': language},
                'commentSource': {'sourceKind': 'snapshot-captured-source', 'sourceHash': hashes[filename]},
                'health': {'status': 'partial', 'score': None, 'components': {'size': 77, 'dependencies': None}}})
    graph = {'nodes': nodes, 'edges': [], 'metadata': {'snapshotId': snapshot.parent.name,
        'rawGraphPath': str(raw)}, 'codeProjects': [
            {'id': 'dotnet-owner', 'manifest': 'dotnet/app/app.csproj'},
            {'id': 'web-owner', 'manifest': 'web/package.json'},
            {'id': 'jvm-owner', 'buildManifest': 'jvm/build.gradle.kts'}]}
    write_json(snapshot, graph)
    write_json(raw, {'nodes': deepcopy(nodes), 'edges': []})
    state.projects[identifier]['path'] = str(root)
    write_json(state.registry, state.projects)
    trace = []

    class TracedProvider:
        """Observe actual provider calls without supplying or replacing analyzer evidence."""
        def __init__(self, declaration, paths):
            self.language = declaration['language']
            self.provider = create_semantic_provider(declaration, paths)
            self.provider_id = self.provider.provider_id

        def probe(self, context, cancel):
            """Record the exact compilation cohort sent to the real discovery implementation."""
            trace.append(('probe', self.language, context, None))
            return self.provider.probe(context, cancel)

        def analyze(self, context, probe, request, cancel):
            """Retain the original-AST request for cross-language/provenance assertions."""
            trace.append(('analyze', self.language, context, deepcopy(request)))
            return self.provider.analyze(context, probe, request, cancel)

    service = SemanticEnrichment(state, provider_factory=TracedProvider)
    try:
        yield SimpleNamespace(state=state, identifier=identifier, snapshot=snapshot, raw=raw,
            root=root, graph=graph, hashes=hashes, service=service, trace=trace,
            source_bytes=snapshot.read_bytes(), raw_bytes=raw.read_bytes())
    finally:
        service.close()
        state.close()


def missing_engines(fixture):
    """Use explicit missing paths so native availability cannot affect fallback assertions."""
    absent = str(fixture.root / 'not-installed')
    for language, (mod_id, strategy) in MODS.items():
        paths = {'dotnetPath': absent, 'workerPath': absent} if language == 'csharp' else {'enginePath': absent}
        fixture.service.settings.save(mod_id, strategy, paths)


def run_selected(fixture):
    """Await only the bounded background operation; production callers return immediately."""
    selected = [f'{language}:Run' for language in COHORTS]
    fixture.service.use(fixture.identifier, fixture.snapshot.parent.name, selected, force=True)
    fixture.service.executor.submit(lambda: None).result(timeout=55)
    return fixture.service.status(fixture.identifier, fixture.snapshot.parent.name)


def assert_cohorts(fixture):
    """Every provider sees only its captured language/owner, despite equal names and shared owners."""
    observed = set()
    for operation, language, context, request in fixture.trace:
        owner, caller, helper = COHORTS[language]
        assert context.target_id == f'{language}:{owner}'
        assert context.snapshot_id == fixture.snapshot.parent.name
        assert context.workspace_id == fixture.identifier
        assert context.source_hashes == {path: fixture.hashes[path] for path in (caller, helper)}
        observed.add(language)
        if operation == 'probe':
            # Missing engines do not build requests in production. Exercise that
            # exact source-only path here to qualify JVM AST anchors separately.
            pinned, contents, _ = build_semantic_request(fixture.root, fixture.graph,
                [f'{language}:Run'], language, owner, context)
            assert set(contents) == {caller, helper}
            assert pinned['scope']['qualified'] and len(pinned['queries']) == 1
            query = pinned['queries'][0]
            assert query['sourceNodeId'] == f'{language}:Run' and query['sourceSpan']['path'] == caller
            left, right = span_bytes(SourceSpan(**query['callsite']), contents[caller])
            assert contents[caller][left:right] in {b'Work(1)', b'Worker.Work(1)', b'Helper.Work(1)'}
        if operation == 'analyze':
            assert {item['path'] for item in request['files']} == {caller, helper}
            assert request['scope']['capturedCohortComplete'] and request['scope']['qualified']
            assert all(item['sourceNodeId'] == f'{language}:Run' for item in request['queries'])
            assert all(item['sourceSpan']['path'] == caller for item in request['queries'])
    assert observed == set(COHORTS)


def assert_immutable(fixture, projected):
    """Optional facts cannot rewrite original snapshots, raw IDs or captured health measurements."""
    assert fixture.snapshot.read_bytes() == fixture.source_bytes
    assert fixture.raw.read_bytes() == fixture.raw_bytes
    assert {item['id'] for item in projected['nodes']} == {item['id'] for item in fixture.graph['nodes']}
    assert all(item['health'] == fixture.graph['nodes'][0]['health'] for item in projected['nodes'])
    assert not (fixture.root / 'dotnet/app/obj').exists() and not (fixture.root / 'dotnet/app/bin').exists()
    assert not (fixture.root / 'jvm/.gradle').exists()


def test_mixed_explicit_missing_baseline_off_and_snapshot_guard(mixed_project):
    """Missing tools preserve one map; disabling one language does not affect other compiler cohorts."""
    fixture = mixed_project
    missing_engines(fixture)
    inventory = {'files': list(SOURCES), 'truncated': False, 'version': 'mixed-fixture',
                 'mode': 'all', 'warnings': []}
    structural = active_analysis_rules(fixture.state.mods, inventory=inventory)
    status = run_selected(fixture)
    assert status['snapshotId'] == fixture.snapshot.parent.name
    assert status['enrichment'] == 'baseline' and status['runtimeSelectionKnown'] is False
    assert {item['language'] for item in status['providers']} == set(COHORTS)
    assert all(item['availability']['status'] in {'missing', 'incompatible'} for item in status['providers'])
    assert not any(row[0] == 'analyze' for row in fixture.trace)
    assert_cohorts(fixture)
    effective = fixture.state.get_graph(fixture.identifier)
    assert effective['edges'] == [] and all('semanticFacts' not in node for node in effective['nodes'])
    assert_immutable(fixture, effective)
    assert not fixture.snapshot.with_name('semantic-overlay.json').exists()
    with pytest.raises(StoreError, match='Карта обновилась'):
        fixture.service.use(fixture.identifier, 'obsolete-snapshot', ['csharp:Run'])
    mod = next(item for item in fixture.state.mods.catalog()['mods'] if item['id'] == 'semantic-typescript')
    fixture.state.mods.enable(mod['id'], mod['fingerprint'], False)
    status = run_selected(fixture)
    assert 'typescript' not in {item['language'] for item in status['providers']}
    assert active_analysis_rules(fixture.state.mods, inventory=inventory) == structural
    assert_immutable(fixture, fixture.state.get_graph(fixture.identifier))


@pytest.mark.parametrize('with_roslyn', [False, True], ids=['installed-ts-js', 'installed-csharp-ts-js'])
def test_real_mixed_engines_share_projection_without_cross_language_calls(mixed_project, engine, with_roslyn):
    """Actual compiler facts share one revision; only closure-proven C# adds a static project edge."""
    fixture = mixed_project
    missing_engines(fixture)
    for language in ('typescript', 'javascript'):
        mod_id, strategy = MODS[language]
        fixture.service.settings.save(mod_id, strategy, {'enginePath': str(engine)})
    # A Python executable is only a context-gate sentinel: missing .classpath rejects
    # Java before engine launch. This does not qualify a JDT installation.
    import sys
    fixture.service.settings.save('semantic-java', 'jdt-local-v1', {'enginePath': sys.executable})
    if with_roslyn:
        dotnet = Path(os.environ.get('ATLAS_TEST_DOTNET', '/usr/local/share/dotnet/dotnet'))
        sdk = Path(os.environ.get('ATLAS_TEST_DOTNET_SDK', '/usr/local/share/dotnet/sdk/8.0.403'))
        worker = Path(os.environ.get('ATLAS_TEST_ROSLYN_WORKER',
                                    str(REPOSITORY / 'build/semantic-csharp/Atlas.Semantic.Roslyn.dll')))
        if not dotnet.is_file() or not sdk.is_dir() or not worker.is_file() or not worker.with_suffix('.runtimeconfig.json').is_file():
            pytest.skip('No existing compatible local Roslyn bridge/SDK; no build or install attempted')
        fixture.service.settings.save('semantic-csharp', 'roslyn-local-v1',
            {'dotnetPath': str(dotnet), 'workerPath': str(worker), 'sdkPath': str(sdk)})
    status = run_selected(fixture)
    assert status['enrichment'] == 'applied', status
    observed = json.loads(fixture.snapshot.with_name('semantic-status.json').read_text())
    assert observed['snapshotId'] == fixture.snapshot.parent.name and observed['partialScope']
    assert observed['runtimeSelectionKnown'] is False
    providers = {item['language']: item for item in status['providers']}
    for language in ('typescript', 'javascript'):
        assert providers[language]['availability']['status'] == 'available', providers[language]
        assert providers[language]['enrichment'] == 'applied'
    assert providers['java']['availability']['status'] == 'incompatible'
    assert providers['kotlin']['availability']['status'] == 'missing'
    if with_roslyn:
        assert providers['csharp']['availability']['status'] == 'available', providers['csharp']
        assert providers['csharp']['enrichment'] == 'applied'
    assert_cohorts(fixture)
    delta = json.loads(fixture.snapshot.with_name('semantic-overlay.json').read_text())
    expected_edges = {('csharp:Run', 'csharp:Work')} if with_roslyn else set()
    assert {(item['source'], item['target']) for item in delta['edges']} == expected_edges
    for language in ('typescript', 'javascript'):
        fact = delta['facts'][f'{language}:Run'][0]
        assert fact['confidence'] == 'candidate'
        assert fact['target']['graph_node_id'] == f'{language}:Work'
        assert fact['runtimeSelectionKnown'] is False
    if with_roslyn:
        assert delta['facts']['csharp:Run'][0]['confidence'] == 'confirmed-static'
        assert delta['facts']['csharp:Run'][0]['target']['graph_node_id'] == 'csharp:Work'
    for source, facts in delta['facts'].items():
        for fact in facts:
            assert fact['target']['graph_node_id'].split(':')[0] == source.split(':')[0]
            assert fact['runtimeSelectionKnown'] is False
    assert not any(key.startswith(('java:', 'kotlin:')) for key in delta['facts'])
    effective = fixture.state.get_graph(fixture.identifier)
    _, health_input = fixture.state.health_snapshot(fixture.identifier, fixture.snapshot.parent.name)
    mcp = json.loads(fixture.state.published_mcp_graph(fixture.identifier).read_text())
    revision = delta['revision']
    for graph in (effective, health_input, mcp['graph']):
        assert graph['metadata']['semanticEnrichment']['revision'] == revision
    for language in ('typescript', 'javascript'):
        node = fixture.state.source_snapshot(fixture.identifier, f'{language}:Run', fixture.snapshot.parent.name)[1]
        assert node['semanticFacts'][0]['target']['graph_node_id'] == f'{language}:Work'
    assert all(item['runtimeSelectionKnown'] is False for item in mcp['links'])
    assert_immutable(fixture, effective)
    # Identical source hashes cannot make a sidecar belong to a different snapshot.
    second = fixture.snapshot.parent.parent / 'second' / fixture.snapshot.name
    write_json(second, fixture.graph)
    write_json(second.with_name('semantic-overlay.json'), delta)
    fixture.state.projects[fixture.identifier]['_graph'] = str(second)
    assert fixture.state.get_graph(fixture.identifier)['edges'] == []
    assert all('semanticFacts' not in node for node in fixture.state.get_graph(fixture.identifier)['nodes'])
    fixture.state.projects[fixture.identifier]['_graph'] = str(fixture.snapshot)
    for language in ('csharp', 'typescript', 'javascript'):
        mod_id, _ = MODS[language]
        mod = next(item for item in fixture.state.mods.catalog()['mods'] if item['id'] == mod_id)
        fixture.state.mods.enable(mod['id'], mod['fingerprint'], False)
    restored = fixture.state.get_graph(fixture.identifier)
    assert restored['edges'] == [] and all('semanticFacts' not in node for node in restored['nodes'])
    assert_immutable(fixture, restored)
