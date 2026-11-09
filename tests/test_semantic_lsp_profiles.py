"""Conditional profiles: metadata safety and missing engines are not native qualification."""
from hashlib import sha256
import json
from pathlib import Path
import sys
from threading import Event

import pytest

from atlas.semantic_models import SemanticContext
from atlas.semantic_lsp_providers import create_lsp_provider
from atlas.semantic_providers.gopls import GoplsProvider
from atlas.semantic_providers.rust_analyzer import RustAnalyzerProvider
from atlas.semantic_providers.sourcekit import SourceKitProvider
from atlas.semantic_providers.jdt import JdtProvider
from atlas.semantic_providers.kotlin import KotlinProvider
from atlas.semantic_runtime import ProbeOutcome
from atlas.semantic_models import SemanticAvailability

HASH = 'a' * 64


def test_kotlin_discovery_never_opens_subject_build_importers(tmp_path, monkeypatch):
    """A version fixture is availability evidence only, not a qualified Kotlin semantic worker."""
    from types import SimpleNamespace
    import atlas.semantic_providers.kotlin as profile
    selected = context(tmp_path, 'main.kt', 'fun work() {}')
    (tmp_path / 'build.gradle.kts').write_text('error("must never run")')
    provider = KotlinProvider('kotlin', engine_path=sys.executable)
    def version(command, *, cwd, environment, **kwargs):
        assert cwd != tmp_path and not (cwd / 'build.gradle.kts').exists()
        assert command[1:] == ['--version']
        return SimpleNamespace(returncode=0, stdout=b'Kotlin LSP 0.261.0', stderr=b'')
    monkeypatch.setattr(profile, 'run_bounded', version)
    probe = provider.probe(selected, Event())
    assert probe.availability.status == 'incompatible'
    assert probe.availability.reason == 'kotlin-static-context-unqualified'
    assert probe.availability.engine_version == 'Kotlin LSP 0.261.0'
    result = provider.analyze(selected, probe, {}, Event())
    assert result.status == 'unsupported' and not result.facts
    assert result.coverage['calls'].status == 'unsupported'


def test_kotlin_missing_path_is_missing_and_never_falls_back(tmp_path):
    selected = context(tmp_path, 'main.kt', 'fun work() {}')
    provider = create_lsp_provider('kotlin', 'kotlin', engine_path=tmp_path / 'missing')
    assert provider.probe(selected, Event()).availability.status == 'missing'


def test_isolated_lsp_binding_stays_candidate_without_dependency_closure(tmp_path, monkeypatch):
    """Protocol evidence tests binding conversion; it never qualifies a native Go engine."""
    import atlas.semantic_providers.lsp_base as base
    texts = {'main.go': 'package demo\nfunc Work() { Helper() }\n',
             'helper.go': 'package demo\nfunc Helper() {}\n'}
    selected = SemanticContext('snapshot', 'workspace', str(tmp_path), 'target', HASH,
        {path: sha256(text.encode()).hexdigest() for path, text in texts.items()}, HASH)
    (tmp_path / 'go.mod').write_text('module example.com/demo\ngo 1.23\n')

    class FixtureSession:
        """A bounded host test double, returning one compiler-shaped declaration location."""
        def __init__(self, command, workspace, *args, **kwargs):
            self.workspace = workspace
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def initialize(self, *args):
            return {}
        def notify(self, *args):
            pass
        def request(self, *args, **kwargs):
            return [{'uri': (self.workspace / 'helper.go').as_uri(),
                     'range': {'start': {'line': 1, 'character': 5}, 'end': {'line': 1, 'character': 11}}}]

    monkeypatch.setattr(base, 'LspSession', FixtureSession)
    span = {'path': 'main.go', 'start_line': 1, 'start_column': 0, 'end_line': 1, 'end_column': 24}
    request = {'files': [{'path': path, 'text': text, 'sourceHash': selected.source_hashes[path]}
                         for path, text in texts.items()], 'queries': [
        {'path': 'main.go', 'sourceNodeId': 'Work', 'label': 'Work', 'kind': 'call', 'line': 1,
         'column': 14, 'sourceSpan': span, 'callsite': {**span, 'start_column': 14, 'end_column': 22}}]}
    probe = ProbeOutcome(SemanticAvailability('available', engine_fingerprint=HASH), (Path(sys.executable),))
    result = GoplsProvider('go').analyze(selected, probe, request, Event())
    assert result.facts[0].target.source_span.path == 'helper.go'
    assert result.facts[0].confidence == 'candidate'
    assert result.coverage['calls'].analyzed == 0
    assert 'project-and-reference-closure-not-proven' in result.coverage['calls'].reasons


def test_java_static_classpath_stages_no_builders_or_processors(tmp_path):
    (tmp_path / '.classpath').write_text('<classpath><classpathentry kind="src" path=""/></classpath>')
    (tmp_path / '.project').write_text('<unsafe-build-command/>')
    (tmp_path / 'pom.xml').write_text('<arbitrary-build-plugin/>')
    workspace = tmp_path / 'captured'
    workspace.mkdir()
    provider = JdtProvider('java')
    provider.prepare_context(tmp_path, workspace, {'Main.java': 'class Main {}'})
    assert 'unsafe-build-command' not in (workspace / '.project').read_text()
    assert '<buildSpec />' in (workspace / '.project').read_text()
    assert not (workspace / 'pom.xml').exists()
    assert 'processAnnotations=disabled' in (workspace / '.settings/org.eclipse.jdt.core.prefs').read_text()
    assert provider.initialization_options()['settings']['java']['import']['gradle']['enabled'] is False
    (tmp_path / '.classpath').write_text('<classpath><classpathentry kind="lib" path="/evil.jar"/></classpath>')
    with pytest.raises(ValueError):
        provider.prepare_context(tmp_path, workspace, {})


def test_java_missing_explicit_engine_and_malformed_context_fall_back(tmp_path):
    selected = context(tmp_path, 'Main.java', 'class Main {}')
    provider = create_lsp_provider('java', 'java', engine_path=tmp_path / 'absent')
    assert provider.probe(selected, Event()).availability.status == 'missing'
    (tmp_path / '.classpath').write_text('<malformed')
    assert JdtProvider('java', engine_path=sys.executable).probe(selected, Event()).availability.status == 'incompatible'


def test_java_native_cross_file_qualification_when_installed(tmp_path):
    provider = JdtProvider('java')
    if not provider.candidates():
        pytest.skip('No existing JDT-LS: nativeQualified=false')
    (tmp_path / '.classpath').write_text('<classpath><classpathentry kind="src" path=""/></classpath>')
    texts = {'Main.java': '// captured\nclass Main { void work() { Helper.run(); } }\n',
             'Helper.java': 'class Helper { static void run() {} }\n'}
    result = native_result(tmp_path, provider, texts, 'Main.java', 'run', 'Helper.run()')
    assert any(fact.confidence == 'candidate' and fact.target.source_span.path == 'Helper.java'
               for fact in result.facts)


def swift_database(root, sdk, flags=()):
    """Compilation data is a fixture, never an executable command."""
    (root / 'compile_commands.json').write_text(json.dumps([{'directory': str(root),
        'file': 'main.swift', 'arguments': ['swiftc', '-sdk', str(sdk), '-module-name', 'Demo',
                                         *flags, 'main.swift', 'helper.swift']}]))


def test_swift_static_database_rejects_plugins_and_omits_build_manifests(tmp_path):
    sdk = tmp_path / 'sdk'
    sdk.mkdir()
    (sdk / 'SDKSettings.json').write_text('{}')
    swift_database(tmp_path, sdk)
    workspace = tmp_path / 'captured'
    workspace.mkdir()
    provider = SourceKitProvider('swift')
    provider.prepare_context(tmp_path, workspace, {'main.swift': 'func work() {}', 'helper.swift': ''})
    options = json.loads((workspace / '.sourcekit-lsp/config.json').read_text())
    assert options['backgroundIndexing'] is False
    assert options['defaultWorkspaceType'] == 'compilationDatabase'
    assert options['fallbackBuildSystem']['sdk'] == str(sdk)
    assert not (workspace / 'Package.swift').exists()
    swift_database(tmp_path, sdk, ['-load-plugin-executable', '/outside/evil'])
    with pytest.raises(ValueError):
        provider.context_assets(tmp_path)


def test_swift_missing_explicit_path_retains_baseline(tmp_path):
    selected = context(tmp_path, 'main.swift', 'func work() {}')
    provider = create_lsp_provider('swift', 'swift', engine_path=tmp_path / 'missing')
    assert provider.probe(selected, Event()).availability.status == 'missing'


def test_swift_native_cross_file_qualification_when_usable(tmp_path):
    provider = SourceKitProvider('swift')
    sdks = [Path('/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk'),
            Path('/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk')]
    sdk = next((path for path in sdks if (path / 'SDKSettings.json').is_file()), None)
    if not provider.candidates() or sdk is None:
        pytest.skip('No compatible installed SourceKit/SDK: nativeQualified=false')
    swift_database(tmp_path, sdk)
    texts = {'main.swift': '// captured\nfunc work() { run() }\n', 'helper.swift': 'func run() {}\n'}
    result = native_result(tmp_path, provider, texts, 'main.swift', 'run', 'run()')
    assert any(fact.confidence == 'candidate' and fact.target.source_span.path == 'helper.swift'
               for fact in result.facts)


def test_rust_export_discards_build_and_macro_context(tmp_path):
    (tmp_path / 'rust-project.json').write_text(json.dumps({'sysroot': '/outside', 'crates': [
        {'root_module': 'main.rs', 'edition': '2021', 'deps': [],
         'proc_macro_dylib_path': '/outside/evil.so', 'env': {'RUSTC_WRAPPER': '/outside/run'}}]}))
    workspace = tmp_path / 'captured'
    workspace.mkdir()
    provider = RustAnalyzerProvider('rust')
    provider.prepare_context(tmp_path, workspace, {'main.rs': 'fn main() {}'})
    actual = json.loads((workspace / 'rust-project.json').read_text())
    assert actual['sysroot'] is None
    assert actual['crates'] == [{'root_module': str(workspace / 'main.rs'), 'edition': '2021',
        'deps': [], 'cfg': [], 'is_workspace_member': True}]
    assert provider.environment(workspace)['RUSTUP_AUTO_INSTALL'] == '0'
    assert provider.initialization_options()['procMacro']['enable'] is False


def test_rust_rejects_external_crate_root_and_missing_explicit_engine(tmp_path):
    selected = context(tmp_path, 'main.rs', 'fn main() {}')
    provider = create_lsp_provider('semantic-rust/rust', 'rust', engine_path=tmp_path / 'missing')
    assert provider.probe(selected, Event()).availability.status == 'missing'
    (tmp_path / 'rust-project.json').write_text(json.dumps({'crates': [
        {'root_module': '../outside.rs', 'edition': '2021'}]}))
    with pytest.raises(ValueError):
        RustAnalyzerProvider._project(tmp_path)


def test_rust_native_cross_file_qualification_when_installed(tmp_path):
    """A real analyzer is optional; mocked protocol evidence is never native evidence."""
    provider = RustAnalyzerProvider('rust')
    if not provider.candidates():
        pytest.skip('No existing rust-analyzer: nativeQualified=false')
    texts = {'main.rs': 'mod helper;\nfn work() { helper::run(); }\n',
             'helper.rs': 'pub fn run() {}\n'}
    (tmp_path / 'rust-project.json').write_text(json.dumps({'crates': [
        {'root_module': 'main.rs', 'edition': '2021', 'deps': []}]}))
    result = native_result(tmp_path, provider, texts, 'main.rs', 'run', 'run()')
    assert any(fact.confidence == 'candidate' and fact.target.source_span.path == 'helper.rs'
               for fact in result.facts)


def native_result(root, provider, texts, caller, identifier, callsite):
    """Capture one actual cross-file request, skipping only unavailable engine/context."""
    for path, text in texts.items():
        (root / path).write_text(text)
    selected = SemanticContext('snapshot', 'workspace', str(root), 'target', HASH,
        {path: sha256(text.encode()).hexdigest() for path, text in texts.items()}, HASH)
    probe = provider.probe(selected, Event())
    if probe.availability.status != 'available':
        pytest.skip(f'No compatible {provider.binary} context: nativeQualified=false; {probe.availability.reason}')
    line = texts[caller].splitlines()[1]
    left = line.index(callsite)
    span = {'path': caller, 'start_line': 1, 'start_column': 0,
            'end_line': 1, 'end_column': len(line)}
    request = {'files': [{'path': path, 'text': text, 'sourceHash': selected.source_hashes[path]}
                         for path, text in texts.items()], 'queries': [
        {'path': caller, 'sourceNodeId': 'work', 'label': 'work', 'kind': 'call',
         'line': 1, 'column': line.index(identifier), 'sourceSpan': span,
         'callsite': {**span, 'start_column': left, 'end_column': left + len(callsite)}}]}
    return provider.analyze(selected, probe, request, Event())


def context(root, filename, text):
    """Pin an exact captured file without requiring a local language SDK."""
    (root / filename).write_text(text)
    return SemanticContext('snapshot', 'workspace', str(root), 'target', HASH,
                           {filename: sha256(text.encode()).hexdigest()}, HASH)


def test_gopls_explicit_missing_path_has_no_automatic_fallback(tmp_path):
    selected = context(tmp_path, 'main.go', 'package demo\nfunc Work() {}\n')
    provider = create_lsp_provider('semantic-go/gopls', 'go', engine_path=tmp_path / 'missing')
    assert provider.probe(selected, Event()).availability.status == 'missing'


def test_go_prepares_static_module_without_original_toolchain_replace_or_hooks(tmp_path):
    (tmp_path / 'go.mod').write_text('module example.com/demo\ngo 1.23\ntoolchain go1.99\nreplace evil => /outside\n')
    workspace = tmp_path / 'captured'
    workspace.mkdir()
    provider = GoplsProvider('go')
    limitations = provider.prepare_context(tmp_path, workspace, {'main.go': 'package demo\n'})
    assert (workspace / 'go.mod').read_text() == 'module example.com/demo\n\ngo 1.23\n'
    assert 'external-module-dependencies-not-loaded' in limitations
    env = provider.environment(workspace)
    assert env['GOTOOLCHAIN'] == 'local' and env['GOPACKAGESDRIVER'] == 'off' and env['CGO_ENABLED'] == '0'


def test_go_requires_static_context_before_engine_launch(tmp_path, monkeypatch):
    selected = context(tmp_path, 'main.go', 'package demo\nfunc Work() {}\n')
    import atlas.semantic_providers.lsp_base as base
    monkeypatch.setattr(base, 'run_bounded', lambda *a, **kw: pytest.fail('engine launched without context'))
    result = GoplsProvider('go', engine_path=sys.executable).probe(selected, Event())
    assert result.availability.status == 'incompatible'


def test_go_and_analyzer_versions_use_private_offline_context(tmp_path, monkeypatch):
    """Even version operations cannot discover subject go.mod toolchain or package hooks."""
    from types import SimpleNamespace
    import atlas.semantic_providers.gopls as profile
    import atlas.semantic_providers.lsp_base as base
    (tmp_path / 'go.mod').write_text('module example.com/demo\ngo 1.23\ntoolchain go1.99\n')
    monkeypatch.setattr(profile, 'discover_executable', lambda *a, **kw: Path(sys.executable))
    calls = []
    def version(command, *, cwd, environment, **kwargs):
        assert cwd != tmp_path and not (cwd / 'go.mod').exists()
        expected = {'GOTOOLCHAIN': 'local', 'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOWORK': 'off',
                    'GOPACKAGESDRIVER': 'off', 'CGO_ENABLED': '0'}
        assert all(environment[key] == value for key, value in expected.items())
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b'go version go1.23\n', stderr=b'')
    monkeypatch.setattr(profile, 'run_bounded', version)
    monkeypatch.setattr(base, 'run_bounded', version)
    provider = GoplsProvider('go')
    provider.context_assets(tmp_path)
    provider.probe_version(Path(sys.executable), tmp_path, Event())
    assert len(calls) == 2


def test_gopls_native_qualification_is_conditional_not_a_protocol_fixture_pass(tmp_path):
    provider = GoplsProvider('go')
    if not provider.candidates():
        pytest.skip('No existing gopls; native cross-file qualification remains pending')
    # A real SDK/context is still necessary even when an engine binary appears.
    texts = {'main.go': 'package demo\nfunc Work() { Helper() }\n',
             'helper.go': 'package demo\nfunc Helper() {}\n'}
    for path, text in texts.items():
        (tmp_path / path).write_text(text)
    selected = SemanticContext('snapshot', 'workspace', str(tmp_path), 'target', HASH,
        {path: sha256(text.encode()).hexdigest() for path, text in texts.items()}, HASH)
    (tmp_path / 'go.mod').write_text('module example.com/demo\ngo 1.23\n')
    probe = provider.probe(selected, Event())
    if probe.availability.status != 'available':
        pytest.skip('Existing gopls has no compatible local SDK/context')
    line = texts['main.go'].splitlines()[1]
    left = line.index('Helper')
    span = {'path': 'main.go', 'start_line': 1, 'start_column': 0,
            'end_line': 1, 'end_column': len(line)}
    request = {'files': [{'path': path, 'text': text, 'sourceHash': selected.source_hashes[path]}
                         for path, text in texts.items()],
        'queries': [{'path': 'main.go', 'sourceNodeId': 'Work', 'label': 'Work', 'kind': 'call',
                     'line': 1, 'column': left, 'sourceSpan': span,
                     'callsite': {**span, 'start_column': left, 'end_column': left + len('Helper()')}}]}
    result = provider.analyze(selected, probe, request, Event())
    assert any(fact.confidence == 'candidate' and fact.target.source_span.path == 'helper.go'
               for fact in result.facts)
