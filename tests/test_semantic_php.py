"""PHP candidate adapter fixtures; synthetic protocol success never means native Phpactor qualification."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from atlas.semantic_models import SemanticContext
from atlas.semantic_providers.php import PhpactorProvider, PHP_FLAGS
from atlas.semantic_process import SemanticProcessError
from atlas.semantic_overlay import make_overlay, write_overlay
from atlas.semantic_sources import declaration_bindings
from atlas.mod_analysis import active_semantic_rules
from atlas.mod_store import ModStore
from atlas.node_description import build_node_description
from atlas.semantic_runtime import SemanticRuntime
from atlas.store import ProjectStore, write_json

H = 'a' * 64
REPOSITORY = Path(__file__).resolve().parents[1]


def fixture(tmp_path):
    """Pin two source declarations and a hostile Composer script/files config which must never execute."""
    sources = {'a.php': '<?php function run(){ return work(); }\n',
               'b.php': '<?php function work(){ return 1; }\n'}
    (tmp_path / 'composer.json').write_text(json.dumps({'scripts': {'post-install-cmd': 'MUST_NOT_RUN'},
        'autoload': {'files': ['bootstrap.php'], 'psr-4': {'Demo\\': 'src/'}}}))
    (tmp_path / '.phpactor.json').write_text(json.dumps({'composer.enable': True, 'indexer.enabled_watchers': ['watchman']}))
    (tmp_path / 'bootstrap.php').write_text('<?php file_put_contents("EXECUTED", "bad");')
    hashes = {}
    for path, text in sources.items():
        (tmp_path / path).write_text(text)
        hashes[path] = sha256(text.encode()).hexdigest()
    context = SemanticContext('snapshot', 'workspace', str(tmp_path), 'php', H, hashes, H)
    row = sources['a.php'].rstrip('\n')
    owner = {'path': 'a.php', 'start_line': 0, 'start_column': row.index('function'),
             'end_line': 0, 'end_column': len(row)}
    left = row.index('work()')
    request = {'files': [{'path': path, 'text': text, 'sourceHash': hashes[path]} for path, text in sources.items()],
        'queries': [{'path': 'a.php', 'sourceNodeId': 'run', 'label': 'run', 'kind': 'call',
                     'line': 0, 'column': left, 'sourceSpan': owner,
                     'callsite': {**owner, 'start_column': left, 'end_column': left + 6}}]}
    return context, sources, request


class ProtocolSession:
    """LSP behavior fixture only; not an installed PHP runtime or Phpactor engine."""
    captured = []
    ambiguous = False
    timeout = False

    def __init__(self, command, workspace, cancel, **options):
        """Inspect the private workspace before responding with source locations."""
        self.workspace, self.cancel = workspace, cancel
        self.command = command
        self.options = options
        assert options['environment']['HOME'].startswith(str(workspace))
        self.__class__.captured.append((command, workspace, options))
        assert not (workspace / '.phpactor.json').exists()
        assert not (workspace / 'bootstrap.php').exists()
        if (workspace / 'composer.json').exists():
            assert json.loads((workspace / 'composer.json').read_text()) == {
                'name': 'atlas/captured', 'autoload': {'classmap': ['a.php', 'b.php']}}

    def __enter__(self):
        """Mirror ownership of the real short-lived transport."""
        return self

    def __exit__(self, *args):
        """Synthetic transport has no real children to clean up."""
        return False

    def initialize(self, workspace):
        """Return the capability the real transport requires during probe/analysis."""
        return {'capabilities': {'definitionProvider': True}}

    def notify(self, *args):
        """Accept only document synchronization used by the adapter."""
        return None

    def request(self, *args):
        """Select exact declaring name ranges in the fixture's captured source files."""
        if self.__class__.timeout:
            raise SemanticProcessError('lsp-request-timeout')
        locations = [{'uri': (self.workspace / 'b.php').as_uri(), 'range': {
            'start': {'line': 0, 'character': 15}, 'end': {'line': 0, 'character': 19}}}]
        if self.__class__.ambiguous:
            locations.append({'uri': (self.workspace / 'a.php').as_uri(), 'range': {
                'start': {'line': 0, 'character': 15}, 'end': {'line': 0, 'character': 18}}})
        return locations


@pytest.fixture
def protocol(monkeypatch, tmp_path_factory):
    """Inject protocol output into real provider lifecycle, explicitly excluding native qualification."""
    import atlas.semantic_providers.php as module
    tool = tmp_path_factory.mktemp('phpactor-protocol') / 'phpactor'
    tool.write_text('synthetic protocol fixture only')
    monkeypatch.setattr(PhpactorProvider, 'installations', lambda self: (Path(sys.executable), (tool,)))

    def run(command, **options):
        """Stand in for PHP/version commands; no interpreter/module installation happens."""
        if '-r' in command:
            return SimpleNamespace(returncode=0, stdout=b'[80200,true,true,true]', stderr=b'')
        assert '--config-extra' in command and '--version' in command
        settings = json.loads(command[command.index('--config-extra') + 1])
        assert settings['composer.enable'] is False and settings['indexer.enabled_watchers'] == []
        return SimpleNamespace(returncode=0, stdout=b'Phpactor 2026.10.07.0', stderr=b'')

    ProtocolSession.captured, ProtocolSession.ambiguous, ProtocolSession.timeout = [], False, False
    monkeypatch.setattr(module, 'run_bounded', run)
    monkeypatch.setattr(module, 'LspSession', ProtocolSession)
    return ProtocolSession


def test_missing_explicit_php_and_engine_do_not_fallback(tmp_path, monkeypatch):
    """An absent host choice stays missing even when another engine could be found automatically."""
    context, _, _ = fixture(tmp_path)
    assert PhpactorProvider('p', php_path=tmp_path / 'missing').probe(context, Event()).availability.status == 'missing'
    monkeypatch.setattr(PhpactorProvider, 'installations', lambda self: (Path(sys.executable), ()))
    availability = PhpactorProvider('p').probe(context, Event()).availability
    assert availability.status == 'missing' and availability.reason == 'phpactor-not-installed'


def test_project_installed_engine_is_refused_before_subject_autoload(tmp_path, monkeypatch):
    """Phpactor's own Composer loader inside the analyzed project must not bootstrap application files."""
    context, _, _ = fixture(tmp_path)
    engine = tmp_path / 'phpactor'
    engine.write_text('not launched')
    monkeypatch.setattr(PhpactorProvider, 'installations', lambda self: (Path(sys.executable), (engine,)))
    availability = PhpactorProvider('p').probe(context, Event()).availability
    assert availability.status == 'incompatible' and 'project-autoloader' in availability.reason


def test_static_context_missing_or_runtime_incompatible(tmp_path, protocol, monkeypatch):
    """The interpreter/version string alone never makes an absent context or extensions ready."""
    context, _, _ = fixture(tmp_path)
    (tmp_path / 'composer.json').unlink()
    assert PhpactorProvider('p').probe(context, Event()).availability.status == 'incompatible'
    (tmp_path / 'composer.json').write_text('{}')
    monkeypatch.setattr(PhpactorProvider, '_runtime_compatible', lambda *args: False)
    status = PhpactorProvider('p').probe(context, Event()).availability
    assert status.status == 'incompatible' and 'extensions' in status.reason


def test_protocol_location_remains_candidate_and_never_executes_bootstrap(tmp_path, protocol):
    """Captured definitions are evidence candidates; dynamic PHP dispatch/context is not proven."""
    context, _, request = fixture(tmp_path)
    provider = PhpactorProvider('p')
    probe = provider.probe(context, Event())
    assert probe.availability.status == 'available'
    result = provider.analyze(context, probe, request, Event())
    assert result.status == 'partial' and len(result.facts) == 1
    assert result.facts[0].confidence == 'candidate'
    assert result.facts[0].target.source_span.path == 'b.php'
    assert result.coverage['calls'].analyzed == 0 and not (tmp_path / 'EXECUTED').exists()
    for command, _, _ in protocol.captured:
        assert tuple(command[1:1 + len(PHP_FLAGS)]) == PHP_FLAGS
        assert json.loads(command[command.index('--config-extra') + 1])['composer.enable'] is False


@pytest.mark.parametrize('mode', ['ambiguous', 'timeout'])
def test_ambiguous_and_unavailable_definitions_stay_nonconfirmed(tmp_path, protocol, mode):
    """Several locations and timeout cannot be interpreted as a completed successful call analysis."""
    context, _, request = fixture(tmp_path)
    provider = PhpactorProvider('p')
    probe = provider.probe(context, Event())
    setattr(protocol, mode, True)
    result = provider.analyze(context, probe, request, Event())
    assert all(fact.confidence != 'confirmed-static' for fact in result.facts)
    assert result.coverage['calls'].analyzed == 0
    assert result.facts[0].confidence == ('unknown' if mode == 'timeout' else 'candidate')


def test_stale_source_context_and_config_files_are_rejected(tmp_path, protocol):
    """Captured text, static manifest and executable config filenames retain independent fences."""
    context, _, request = fixture(tmp_path)
    provider = PhpactorProvider('p')
    probe = provider.probe(context, Event())
    request['files'][0]['text'] += 'changed'
    with pytest.raises(ValueError, match='captured bytes'):
        provider.analyze(context, probe, request, Event())
    context, _, request = fixture(tmp_path)
    (tmp_path / 'composer.json').write_text('{"name":"changed"}')
    with pytest.raises(ValueError, match='changed after discovery'):
        provider.analyze(context, probe, request, Event())


def test_optional_mod_candidate_facts_reach_store_description_and_off(tmp_path, protocol):
    """Protocol candidates stay visible as evidence without adding a confirmed graph edge or mutating baseline."""
    context, sources, request = fixture(tmp_path)
    mods = ModStore(tmp_path / 'state', bundled_dir=REPOSITORY / 'mods')
    rules = active_semantic_rules(mods, inventory={'files': list(sources), 'truncated': False})
    selected = next(rule for rule in rules['semanticProviders'] if rule['modId'] == 'semantic-php')
    context = replace(context, provider_fingerprint=selected['modFingerprint'])
    provider = PhpactorProvider('semantic-php/phpactor')
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    graph = {'nodes': [{'id': name, 'label': name + '()', 'kind': 'method', 'file': path, 'line': 1,
        'memberFacts': {'name': name, 'language': 'php'},
        'commentSource': {'sourceKind': 'snapshot-captured-source', 'sourceHash': context.source_hashes[path]}}
        for name, path in [('run', 'a.php'), ('work', 'b.php')]], 'edges': [], 'metadata': {'snapshotId': 'snapshot'}}
    snapshot = tmp_path / 'state/workspace/snapshot/atlas-graph.json'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(graph))
    contents, bindings = declaration_bindings(tmp_path, graph, sources, 'snapshot', context.source_hashes)
    overlay = make_overlay(snapshot, graph, tmp_path, [result], bindings, contents, rules)
    assert overlay['edges'] == []
    state = ProjectStore(tmp_path / 'state')
    try:
        state.projects['workspace'] = {'id': 'workspace', 'name': 'PHP fixture', 'path': str(tmp_path),
            'status': 'completed', '_graph': str(snapshot), '_rawGraph': str(snapshot)}
        write_json(state.registry, state.projects)
        original = snapshot.read_bytes()
        write_overlay(snapshot, overlay)
        _, effective = state.comparison_snapshot('workspace')
        description = build_node_description(tmp_path, effective, 'run', 'snapshot')
        assert description['semanticFacts'][0]['confidence'] == 'candidate'
        assert description['semanticFacts'][0]['runtimeSelectionKnown'] is False
        item = next(item for item in state.mods.catalog()['mods'] if item['id'] == 'semantic-php')
        state.mods.enable(item['id'], item['fingerprint'], False)
        _, disabled = state.comparison_snapshot('workspace')
        assert not disabled['nodes'][0].get('semanticFacts') and disabled['edges'] == []
        assert snapshot.read_bytes() == original
    finally:
        state.close()


def test_native_phpactor_is_conditional_and_absence_is_not_native_pass(tmp_path):
    """Run only an already installed compatible engine; never download PHP or Composer dependencies."""
    context, _, request = fixture(tmp_path)
    provider = PhpactorProvider('p')
    probe = provider.probe(context, Event())
    if probe.availability.status != 'available':
        pytest.skip('nativeQualified=false: ' + probe.availability.reason)
    result = provider.analyze(context, probe, request, Event())
    assert any(fact.target is not None and fact.target.source_span is not None
               and fact.target.source_span.path == 'b.php' for fact in result.facts)


def test_runtime_off_cancels_pending_php_analysis(tmp_path, protocol, monkeypatch):
    """Even a late protocol response cannot publish candidate facts after the user switches the mod off."""
    import atlas.semantic_providers.php as module
    context, _, request = fixture(tmp_path)
    provider = PhpactorProvider('p')
    entered, release = Event(), Event()

    class GatedSession(protocol):
        """Pause only source analysis, leaving the discovery handshake free to complete."""
        def __init__(self, command, workspace, cancel, **options):
            if (workspace / 'a.php').exists():
                entered.set()
                release.wait(2)
            super().__init__(command, workspace, cancel, **options)

    monkeypatch.setattr(module, 'LspSession', GatedSession)
    runtime = SemanticRuntime(tmp_path / 'cache', {'p': provider})
    try:
        runtime.on_use(context, ['p'])
        assert runtime.wait_for_idle()
        future = runtime.submit_analysis(context, 'p', request)
        assert entered.wait(2)
        runtime.cancel(context, ['p'])
        release.set()
        assert future.result(3) is None
        assert not runtime.result_for(context, ['p'])
    finally:
        release.set()
        runtime.close()


def test_executable_project_config_cannot_be_staged_as_a_source(tmp_path, protocol):
    """Captured filenames do not override the fixed engine configuration boundary."""
    context, _, request = fixture(tmp_path)
    provider = PhpactorProvider('p')
    probe = provider.probe(context, Event())
    text = '<?php throw new Exception("must not run");'
    digest = sha256(text.encode()).hexdigest()
    context = replace(context, source_hashes={**context.source_hashes, '.phpactor.php': digest})
    request['files'].append({'path': '.phpactor.php', 'text': text, 'sourceHash': digest})
    with pytest.raises(ValueError, match='configuration'):
        provider.analyze(context, probe, request, Event())


def test_host_factory_preserves_explicit_php_and_engine_paths(tmp_path):
    """The application owns path choices; a semantic manifest never selects commands."""
    from atlas.semantic_provider_registry import create_semantic_provider
    provider = create_semantic_provider({'modId': 'semantic-php', 'id': 'phpactor',
        'strategy': 'phpactor-local-v1', 'language': 'php'},
        {'enginePath': str(tmp_path / 'engine.phar'), 'phpPath': str(tmp_path / 'php')})
    assert isinstance(provider, PhpactorProvider)
    assert provider.engine_path == str(tmp_path / 'engine.phar')
    assert provider.php_path == str(tmp_path / 'php')
