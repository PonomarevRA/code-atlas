"""Actual local SDK Roslyn bindings without restoring, building or running the analyzed project."""
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from threading import Event

import pytest

from atlas.method_flow_chunks import _parse, _select
from atlas.semantic_models import SemanticContext, SourceSpan
from atlas.semantic_providers.roslyn import RoslynProvider
from atlas.semantic_providers.roslyn_context import ContextUnavailable, resolve_configuration
from atlas.semantic_sources import declaration_bindings
from atlas.semantic_overlay import make_overlay, write_overlay
from atlas.mod_analysis import active_semantic_rules
from atlas.mod_store import ModStore
from atlas.semantic_process import SemanticProcessError
from atlas.semantic_runtime import SemanticRuntime
from atlas.node_description import build_node_description
from atlas.store import ProjectStore, write_json

REPOSITORY = Path(__file__).resolve().parents[1]
DOTNET = Path('/usr/local/share/dotnet/dotnet')
SDK = Path('/usr/local/share/dotnet/sdk/8.0.403')
H = 'a' * 64


@pytest.fixture(scope='module')
def worker(tmp_path_factory):
    """Compile only the Atlas bridge from the installed compiler and existing net8 reference pack."""
    if not DOTNET.is_file() or not SDK.is_dir():
        pytest.skip('Pinned local SDK8 fixture unavailable; no engine installation is attempted')
    path = REPOSITORY / 'resources/semantic-csharp/build_worker.py'
    spec = importlib.util.spec_from_file_location('atlas_roslyn_build', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_worker(DOTNET, SDK, tmp_path_factory.mktemp('atlas-roslyn-worker'))


def setup_project(tmp_path, sources, project=None):
    """Write a literal net8 target and pinned source cohort; no project build artifacts exist."""
    manifest = project or '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>'
    (tmp_path / 'fixture.csproj').write_text(manifest)
    hashes = {}
    for path, text in sources.items():
        (tmp_path / path).write_text(text)
        hashes[path] = sha256(text.encode()).hexdigest()
    return SemanticContext('snapshot', 'workspace', str(tmp_path), 'net8.0', H, hashes, H)


def span(text, start, end):
    """Convert Python character offsets to the worker's explicit UTF16 line/column coordinates."""
    before, through = text[:start], text[:end]
    return {'line': before.count('\n'), 'column': len(before.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2,
            'endLine': through.count('\n'), 'endColumn': len(through.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def query(text, expression, *, name='Run', path='a.cs', source_id='run'):
    """Select the real original method span rather than reconstructing its braces from strings."""
    tree, _ = _parse(text.encode(), path)
    line = next(index + 1 for index, row in enumerate(text.splitlines()) if name + '(' in row)
    selected, reason = _select(tree.root_node, {'line': line, 'label': name + '()', 'kind': 'method'}, text.encode())
    assert selected is not None, reason
    source = text.encode()
    owner_start = len(source[:selected.start_byte].decode())
    owner_end = len(source[:selected.end_byte].decode())
    owner = span(text, owner_start, owner_end)
    start = text.index(expression)
    site = {'path': path, **span(text, start, start + len(expression))}
    source_span = {'path': path, 'start_line': owner['line'], 'start_column': owner['column'],
                   'end_line': owner['endLine'], 'end_column': owner['endColumn'], 'coordinate_kind': 'utf-16'}
    return {**site, 'kind': 'call', 'sourceNodeId': source_id, 'label': name, 'sourceSpan': source_span}


def analyze(provider, context, sources, queries):
    """Exercise the production provider/probe and process boundary with actual compiler output."""
    discovery = provider.probe(context, Event())
    assert discovery.availability.status == 'available', discovery.availability
    request = {'files': [{'path': path, 'text': text, 'sourceHash': context.source_hashes[path]}
                         for path, text in sources.items()], 'queries': queries}
    return provider.analyze(context, discovery, request, Event()), discovery, request


def test_actual_roslyn_cross_file_extension_overload_and_framework_api(tmp_path, worker):
    """Bind original receiver types and overloads; common framework calls are external metadata facts."""
    sources = {
        'a.cs': '''using System.Linq;
using System.Text.Json;
class A {
 public static int Run() {
  var values = new[] { 1, 2 };
  var filtered = values.Where(x => x > 0).ToList();
  var found = filtered.Contains(1);
  var value = JsonSerializer.Deserialize<int>("1");
  var name = nameof(B.Work);
  return B.Work(value);
 }
}''',
        'b.cs': 'class B { public static int Work(int value) => value; public static string Work(string value) => value; }',
    }
    context = setup_project(tmp_path, sources)
    provider = RoslynProvider('semantic-csharp/roslyn', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    expressions = ['values.Where(x => x > 0)', 'values.Where(x => x > 0).ToList()',
                   'filtered.Contains(1)', 'JsonSerializer.Deserialize<int>("1")', 'nameof(B.Work)', 'B.Work(value)']
    result, discovery, _ = analyze(provider, context, sources, [query(sources['a.cs'], value) for value in expressions])
    assert result.status == 'partial' and len(result.facts) == 5
    assert all(fact.confidence == 'confirmed-static' for fact in result.facts)
    project = result.facts[-1]
    assert project.target.source_span.path == 'b.cs' and 'System.Int32' in project.target.symbol_id
    assert all(fact.target.uri.startswith('assembly:') for fact in result.facts[:-1])
    where = json.loads(result.facts[0].evidence)
    assert where['extensionMethod'] and where['receiverType'] == 'int[]'
    assert 'nameof-excluded-from-calls' in result.coverage['definition'].reasons
    assert discovery.availability.engine_version.startswith('4.11.')
    assert not (tmp_path / 'obj').exists() and not (tmp_path / 'bin').exists()


def test_ambiguity_and_missing_project_dependency_stay_unknown(tmp_path, worker):
    """Roslyn candidate failure and absent project links are never promoted to external API success."""
    sources = {'a.cs': '''class A {
 public static int Run(){
  B.Work(null);
  return MissingProject.Call();
 }
}''', 'b.cs': 'class B { public static void Work(string value){} public static void Work(System.Uri value){} }'}
    context = setup_project(tmp_path, sources)
    provider = RoslynProvider('semantic-csharp/roslyn', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    result, _, _ = analyze(provider, context, sources, [query(sources['a.cs'], 'B.Work(null)'),
                                                       query(sources['a.cs'], 'MissingProject.Call()')])
    assert len(result.facts) == 2
    assert all(fact.confidence == 'unknown' and fact.target is None for fact in result.facts)
    assert result.coverage['definition'].analyzed == 0


def test_framework_name_shadowing_binds_project_method(tmp_path, worker):
    """A local JsonSerializer shadows the imported .NET name and stays a project-source binding."""
    sources = {'a.cs': '''using System.Text.Json;
class A {
 public static int Run(){ return JsonSerializer.Deserialize(1); }
}''', 'b.cs': 'class JsonSerializer { public static int Deserialize(int value) => value; }'}
    context = setup_project(tmp_path, sources)
    provider = RoslynProvider('semantic-csharp/roslyn', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    result, _, _ = analyze(provider, context, sources, [query(sources['a.cs'], 'JsonSerializer.Deserialize(1)')])
    assert result.facts[0].target.source_span.path == 'b.cs'
    assert result.facts[0].target.uri is None
    assert json.loads(result.facts[0].evidence)['origin'] == 'project-source'


def test_hash_context_and_conditional_compilation_fences(tmp_path, worker):
    """Changed payloads/configuration and unspecified conditional symbols cannot produce bindings."""
    sources = {'a.cs': 'class A { public static int Run(){ return B.Work(1); } }',
               'b.cs': 'class B { public static int Work(int value) => value; }'}
    context = setup_project(tmp_path, sources)
    provider = RoslynProvider('semantic-csharp/roslyn', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    result, discovery, request = analyze(provider, context, sources, [query(sources['a.cs'], 'B.Work(1)')])
    request['files'][0]['text'] += '// changed'
    with pytest.raises(ValueError, match='payload changed'):
        provider.analyze(context, discovery, request, Event())
    (tmp_path / 'fixture.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework><LangVersion>11.0</LangVersion></PropertyGroup></Project>')
    with pytest.raises(ValueError, match='changed after discovery'):
        provider.analyze(context, discovery, {'files': [{'path': path, 'text': text, 'sourceHash': context.source_hashes[path]}
                                                       for path, text in sources.items()], 'queries': []}, Event())
    conditional = {'a.cs': 'class A {\n public static int Run(){\n#if DEBUG\n return 1;\n#else\n return 2;\n#endif\n}\n}'}
    context = setup_project(tmp_path, conditional)
    discovery = provider.probe(context, Event())
    result = provider.analyze(context, discovery, {'files': [{'path': 'a.cs', 'text': conditional['a.cs'],
        'sourceHash': context.source_hashes['a.cs']}], 'queries': []}, Event())
    assert not result.facts and 'conditional-compilation-symbols-not-explicit' in result.coverage['definition'].reasons


def test_missing_worker_runtime_and_context_are_honest(tmp_path, worker):
    """Installed dotnet alone never makes a bridge or unknown MSBuild target available."""
    sources = {'a.cs': 'class A {}'}
    context = setup_project(tmp_path, sources)
    assert RoslynProvider('p', worker_path=tmp_path / 'absent.dll').probe(context, Event()).availability.status == 'incompatible'
    assert RoslynProvider('p', worker_path=worker, dotnet=tmp_path / 'dotnet').probe(context, Event()).availability.status == 'missing'
    project = tmp_path / 'fixture.csproj'
    project.write_text('<Project Sdk="Microsoft.NET.Sdk"><Import Project="evil.targets"/></Project>')
    status = RoslynProvider('p', worker_path=worker, dotnet=DOTNET).probe(context, Event()).availability
    assert status.status == 'incompatible' and status.reason == 'project-context-requires-msbuild'
    project.unlink()
    assert RoslynProvider('p', worker_path=worker, dotnet=DOTNET).probe(context, Event()).availability.reason == 'literal-target-framework-required'


def test_multitarget_and_missing_restored_assets_refuse_guessing(tmp_path, worker):
    """No implicit target choice, package restore or source-project target execution is permitted."""
    context = setup_project(tmp_path, {'a.cs': 'class A {}'},
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFrameworks>net8.0;net9.0</TargetFrameworks></PropertyGroup></Project>')
    with pytest.raises(ContextUnavailable, match='literal-target'):
        resolve_configuration(context, dotnet=DOTNET)
    configuration = resolve_configuration(context, dotnet=DOTNET, target_framework='net8.0')
    assert configuration.framework == 'net8.0'
    (tmp_path / 'fixture.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup><ItemGroup><PackageReference Include="NeverDownload" Version="1"/></ItemGroup></Project>')
    with pytest.raises(ContextUnavailable, match='restored-package-context-missing'):
        resolve_configuration(context, dotnet=DOTNET)
    assert not (tmp_path / 'obj').exists()


def test_worker_result_flows_into_real_optional_overlay(tmp_path, worker):
    """Actual compiler locations reach the production graph mapper while preserving raw IDs and unknown status."""
    sources = {'a.cs': 'class A { public static int Run(){ return B.Work(1); } }',
               'b.cs': 'class B { public static int Work(int value) => value; }'}
    context = setup_project(tmp_path, sources)
    mods = ModStore(tmp_path / 'state', bundled_dir=REPOSITORY / 'mods')
    rules = active_semantic_rules(mods, inventory={'files': list(sources), 'truncated': False})
    provider_rule = next(rule for rule in rules['semanticProviders'] if rule['modId'] == 'semantic-csharp')
    context = replace(context, provider_fingerprint=provider_rule['modFingerprint'])
    provider = RoslynProvider('semantic-csharp/roslyn', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    result, _, _ = analyze(provider, context, sources, [query(sources['a.cs'], 'B.Work(1)')])
    graph = {'nodes': [{'id': name, 'kind': 'method', 'label': name.capitalize() + '()', 'file': path, 'line': 1,
        'memberFacts': {'name': name.capitalize()}, 'commentSource': {'sourceKind': 'snapshot-captured-source',
        'sourceHash': context.source_hashes[path]}} for name, path in [('run', 'a.cs'), ('work', 'b.cs')]],
        'edges': [], 'metadata': {'snapshotId': 'snapshot'}}
    snapshot = tmp_path / 'state' / 'workspace' / 'snapshot' / 'atlas-graph.json'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(graph))
    contents, bindings = declaration_bindings(tmp_path, graph, sources, 'snapshot', context.source_hashes)
    overlay = make_overlay(snapshot, graph, tmp_path, [result], bindings, contents, rules)
    assert overlay['edges'][0]['source'] == 'run' and overlay['edges'][0]['target'] == 'work'
    assert overlay['edges'][0]['runtimeSelectionKnown'] is False
    assert graph['edges'] == [] and all('semanticFacts' not in node for node in graph['nodes'])
    state = ProjectStore(tmp_path / 'state')
    try:
        state.projects['workspace'] = {'id': 'workspace', 'name': 'C# fixture', 'path': str(tmp_path),
            'status': 'completed', '_graph': str(snapshot), '_rawGraph': str(snapshot)}
        write_json(state.registry, state.projects)
        original = snapshot.read_bytes()
        write_overlay(snapshot, overlay)
        _, effective = state.comparison_snapshot('workspace')
        description = build_node_description(tmp_path, effective, 'run', 'snapshot')
        assert description['semanticFacts'][0]['target']['graph_node_id'] == 'work'
        assert description['semanticFacts'][0]['runtimeSelectionKnown'] is False
        item = next(item for item in state.mods.catalog()['mods'] if item['id'] == 'semantic-csharp')
        state.mods.enable(item['id'], item['fingerprint'], False)
        assert state.comparison_snapshot('workspace')[1]['edges'] == []
        assert snapshot.read_bytes() == original
    finally:
        state.close()


def test_off_and_transport_timeout_never_publish_success(tmp_path, worker, monkeypatch):
    """The real provider preserves shared-runtime cancellation and typed process failure semantics."""
    sources = {'a.cs': 'class A { public static int Run(){ return 1; } }'}
    context = setup_project(tmp_path, sources)
    provider = RoslynProvider('semantic-csharp/roslyn', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    runtime = SemanticRuntime(tmp_path / 'cache', {provider.provider_id: provider})
    import atlas.semantic_providers.roslyn as module
    original = module.run_bounded
    entered, release = Event(), Event()

    def gated(command, **options):
        """Pause at the actual provider process boundary so Off happens before analyzer launch."""
        if '--probe' not in command:
            entered.set()
            release.wait(2)
        return original(command, **options)

    try:
        runtime.on_use(context, [provider.provider_id])
        assert runtime.wait_for_idle()
        request = {'files': [{'path': 'a.cs', 'text': sources['a.cs'], 'sourceHash': context.source_hashes['a.cs']}], 'queries': []}
        monkeypatch.setattr(module, 'run_bounded', gated)
        future = runtime.submit_analysis(context, provider.provider_id, request)
        assert entered.wait(2)
        runtime.cancel(context, [provider.provider_id])
        release.set()
        assert future.result(3) is None and not runtime.result_for(context, [provider.provider_id])
        monkeypatch.setattr(module, 'run_bounded', original)
        discovery = provider.probe(context, Event())

        def timeout(*args, **kwargs):
            """A bounded transport fault must propagate as a fault, never an empty successful result."""
            raise SemanticProcessError('timeout')

        monkeypatch.setattr(module, 'run_bounded', timeout)
        with pytest.raises(SemanticProcessError, match='timeout'):
            provider.analyze(context, discovery, request, Event())
    finally:
        release.set()
        runtime.close()


def test_bom_unicode_and_default_installed_sdk(tmp_path, worker):
    """The app's automatic SDK strategy binds UTF16 queries while retaining original BOM byte hashes."""
    sources = {'a.cs': 'class A {\n public static int Run(){ var s = "😀"; return B.Work(1); }\n}',
               'b.cs': 'class B { public static int Work(int value) => value; }'}
    context = setup_project(tmp_path, sources)
    encoded = b'\xef\xbb\xbf' + sources['a.cs'].encode()
    (tmp_path / 'a.cs').write_bytes(encoded)
    context = replace(context, source_hashes={**context.source_hashes, 'a.cs': sha256(encoded).hexdigest()})
    provider = RoslynProvider('semantic-csharp/roslyn', dotnet=DOTNET, worker_path=worker)
    result, _, _ = analyze(provider, context, sources, [query(sources['a.cs'], 'B.Work(1)')])
    assert result.facts[0].confidence == 'confirmed-static'
    assert result.facts[0].target.source_span.path == 'b.cs'


def test_omitted_shadow_source_and_explicit_fragment_are_candidates(tmp_path, worker):
    """A fragment's BCL binding cannot become confirmed when an omitted source shadows that API."""
    sources = {'a.cs': 'using System.Text.Json; class A { public static int Run(){ return JsonSerializer.Deserialize<int>("1"); } }'}
    context = setup_project(tmp_path, sources)
    (tmp_path / 'omitted.cs').write_text('class JsonSerializer { public static T Deserialize<T>(string input) => default!; }')
    provider = RoslynProvider('p', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    queries = [query(sources['a.cs'], 'JsonSerializer.Deserialize<int>("1")')]
    result, _, _ = analyze(provider, context, sources, queries)
    assert result.facts[0].confidence == 'candidate' and result.facts[0].target.uri.startswith('assembly:')
    assert result.coverage['definition'].analyzed == 0
    assert 'project-source-closure-incomplete' in result.coverage['definition'].reasons
    (tmp_path / 'fixture.csproj').unlink()
    provider = RoslynProvider('p', dotnet=DOTNET, sdk_root=SDK, worker_path=worker, target_framework='net8.0')
    result, _, _ = analyze(provider, context, sources, queries)
    assert result.facts[0].confidence == 'candidate'
    assert 'explicit-fragment-without-project-closure' in result.coverage['definition'].reasons


@pytest.mark.parametrize('context_kind', ['props', 'implicit', 'inventory-budget', 'stale'])
def test_unproven_build_context_never_confirms(tmp_path, worker, context_kind):
    """Inherited properties, generated imports, stale captures and bounded scans never prove a target."""
    sources = {'a.cs': 'class A { public static int Run(){ return System.Math.Abs(-1); } }'}
    context = setup_project(tmp_path, sources)
    if context_kind == 'props':
        (tmp_path / 'Directory.Build.props').write_text('<Project><PropertyGroup><DefineConstants>SHADOW</DefineConstants></PropertyGroup></Project>')
    elif context_kind == 'implicit':
        (tmp_path / 'fixture.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings></PropertyGroup></Project>')
    elif context_kind == 'inventory-budget':
        for index in range(130):
            (tmp_path / str(index)).mkdir()
    else:
        (tmp_path / 'a.cs').write_text(sources['a.cs'] + '// changed')
    provider = RoslynProvider('p', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    result, _, _ = analyze(provider, context, sources, [query(sources['a.cs'], 'System.Math.Abs(-1)')])
    assert result.facts[0].confidence == 'candidate'
    assert result.coverage['definition'].analyzed == 0


def test_project_errors_outside_query_cannot_prove_binding(tmp_path, worker):
    """A syntactically successful call inside an invalid assembly remains candidate evidence."""
    sources = {'a.cs': 'class A { public static int Run(){ return System.Math.Abs(-1); } }',
               'b.cs': 'class B {} class B {}'}
    context = setup_project(tmp_path, sources)
    provider = RoslynProvider('p', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    result, _, _ = analyze(provider, context, sources, [query(sources['a.cs'], 'System.Math.Abs(-1)')])
    assert result.facts[0].confidence == 'candidate'
    assert 'compilation-has-errors-or-incomplete-diagnostics' in result.coverage['definition'].reasons


def test_oversized_symbol_identity_is_refused_not_truncated(tmp_path, worker):
    """Different long symbol identities cannot collapse into the same truncated target."""
    long_name = 'Work' + 'x' * 2050
    sources = {'a.cs': 'class A { public static int Run(){ return B.' + long_name + '(); } }',
               'b.cs': 'class B { public static int ' + long_name + '() => 1; }'}
    context = setup_project(tmp_path, sources)
    provider = RoslynProvider('p', dotnet=DOTNET, sdk_root=SDK, worker_path=worker)
    result, _, _ = analyze(provider, context, sources, [query(sources['a.cs'], 'B.' + long_name + '()')])
    assert result.facts[0].confidence == 'unknown' and result.facts[0].target is None
    assert 'symbol-identity-budget' in result.coverage['definition'].reasons
