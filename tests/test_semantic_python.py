"""Jedi protocol/fallback contracts; real-engine qualification runs only when already installed."""
from dataclasses import asdict
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import os
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from atlas.mod_analysis import active_analysis_rules, active_semantic_rules
from atlas.mod_store import ModStore
from atlas.semantic_models import SemanticContext
from atlas.semantic_process import SemanticProcessError
from atlas.semantic_providers.python import JediProvider, _request
from atlas.semantic_providers import python_worker

HASH = 'a' * 64


def source_request(tmp_path):
    """Pin a caller and helper without evaluating their Python code."""
    texts = {'main.py': 'from helper import run\ndef main():\n    return run(1)\n',
             'helper.py': 'def run(value):\n    return value + 1\n'}
    files = [{'path': path, 'text': text, 'sourceHash': sha256(text.encode()).hexdigest()}
             for path, text in texts.items()]
    source = {'path': 'main.py', 'start_line': 1, 'start_column': 0, 'end_line': 2,
              'end_column': 17, 'coordinate_kind': 'utf-16'}
    site = {**source, 'start_line': 2, 'start_column': 11}
    query = {'sourceNodeId': 'main', 'path': 'main.py', 'line': 2, 'column': 11,
             'kind': 'call', 'sourceSpan': source, 'callsite': site, 'label': 'main'}
    context = SemanticContext('snapshot', 'workspace', str(tmp_path), 'python', HASH,
                               {item['path']: item['sourceHash'] for item in files}, HASH)
    return context, {'files': files, 'queries': [query]}


def test_actual_selected_python_probe_never_assumes_jedi(tmp_path):
    """The existing selected interpreter reports its real installed analyzer availability."""
    context, _ = source_request(tmp_path)
    provider = JediProvider('semantic-python/jedi', python_path=sys.executable)
    state = provider.probe(context, Event())
    if importlib.util.find_spec('jedi') is None:
        assert state.availability.status == 'missing' and state.availability.reason == 'jedi-not-installed'
    else:
        assert state.availability.status in {'available', 'incompatible'}
    assert 'jedi' not in sys.modules


def test_explicit_missing_python_does_not_fallback(tmp_path):
    """An invalid user override must not silently select the system interpreter."""
    context, _ = source_request(tmp_path)
    state = JediProvider('jedi', python_path=tmp_path / 'missing-python').probe(context, Event())
    assert state.availability.status == 'missing' and state.availability.reason == 'python-not-found'


def test_stale_source_and_outside_declaration_queries_are_refused(tmp_path):
    """Source hashes, UTF-16 units, paths and owning declaration ranges are hard boundaries."""
    context, request = source_request(tmp_path)
    request['files'][0]['text'] += '#changed'
    with pytest.raises(ValueError, match='stale'):
        _request(context, request)
    context, request = source_request(tmp_path)
    request['queries'][0]['callsite']['start_line'] = 0
    with pytest.raises(ValueError, match='outside'):
        _request(context, request)
    context, request = source_request(tmp_path)
    request['files'][0]['path'] = '../outside.py'
    with pytest.raises(ValueError):
        _request(context, request)


def test_jedi_protocol_resolve_preserves_external_and_dynamic_unknown(tmp_path):
    """A protocol fixture verifies source anchors; it is not an installed-Jedi qualification."""
    texts = {'main.py': 'run()\n', 'helper.py': 'def run(): pass\n'}
    query = {'path': 'main.py', 'line': 0, 'column': 0, 'kind': 'call'}

    class Script:
        """Stand-in API returns a mix of source, dynamic parameter and external definitions."""
        def goto(self, line, column, **options):
            """Require static import-following and refusal of compiled/builtin imports."""
            assert options == {'follow_imports': True, 'follow_builtin_imports': False}
            return [SimpleNamespace(module_path=tmp_path / 'helper.py', line=1, column=4,
                                    name='run', type='function'),
                    SimpleNamespace(module_path=None, line=None, column=None, name='print', type='function'),
                    SimpleNamespace(module_path=tmp_path / 'main.py', line=1, column=0,
                                    name='run', type='param')]

    result = python_worker.resolve(Script(), query, texts, tmp_path)
    assert len(result['targets']) == 1
    assert result['targets'][0]['source_span']['path'] == 'helper.py'
    assert result['targets'][0]['source_span']['start_column'] == 4


def test_worker_stages_sources_and_never_executes_application_protocol_fixture(tmp_path, monkeypatch):
    """The worker's production staging path treats application code as text in an explicit project."""
    _, request = source_request(tmp_path)
    request['files'][1]['text'] = 'raise RuntimeError("application must not execute")\n'
    captured = []

    class Project:
        """Protocol stand-in records the safe source-only Jedi options."""
        def __init__(self, directory, **options):
            assert options == {'sys_path': [str(directory)], 'smart_sys_path': False, 'load_unsafe_extensions': False}
            assert directory != tmp_path
            assert (directory / 'helper.py').read_text().startswith('raise RuntimeError')
            captured.append(directory)

    class Script:
        """The API analyzes text and returns no statically known target."""
        def __init__(self, code, **options):
            assert code.startswith('from helper') and options['path'].parent in captured

        def goto(self, *args, **kwargs):
            """Dynamic lookup remains empty and unknown."""
            return []

    monkeypatch.setitem(sys.modules, 'jedi', SimpleNamespace(Project=Project, Script=Script))
    monkeypatch.setitem(sys.modules, 'parso', SimpleNamespace())
    monkeypatch.setattr(sys, 'argv', ['python_worker.py'])
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', output)
    python_worker.main()
    response = json.loads(output.getvalue())
    assert response['results'][0]['targets'] == []
    assert not captured[0].exists()


def test_semantic_python_off_retains_source_baseline(tmp_path):
    """The actual declarative mod is independent of project discovery and optional engine state."""
    store = ModStore(tmp_path, Path(__file__).resolve().parents[1] / 'mods')
    inventory = {'files': ['main.py'], 'truncated': False, 'mode': 'all', 'version': 'test', 'warnings': []}
    baseline = active_analysis_rules(store, inventory=inventory)
    assert any(item['language'] == 'python' for item in active_semantic_rules(store, inventory=inventory)['semanticProviders'])
    item = next(item for item in store.catalog()['mods'] if item['id'] == 'semantic-python')
    store.enable(item['id'], item['fingerprint'], False)
    assert not any(item['language'] == 'python' for item in active_semantic_rules(store, inventory=inventory)['semanticProviders'])
    assert active_analysis_rules(store, inventory=inventory) == baseline


def test_real_jedi_cross_file_definition_if_already_installed(tmp_path):
    """Run real engine qualification only against an existing selected Jedi installation."""
    context, request = source_request(tmp_path)
    python = os.environ.get('ATLAS_TEST_JEDI_PYTHON', sys.executable)
    provider = JediProvider('semantic-python/jedi', python_path=python)
    probe = provider.probe(context, Event())
    if probe.availability.status != 'available':
        pytest.skip('No compatible installed Jedi; real positive qualification remains pending')
    result = provider.analyze(context, probe, request, Event())
    assert any(fact.target and fact.target.source_span.path == 'helper.py' for fact in result.facts)
    assert all(fact.kind == 'reference' for fact in result.facts)
    assert result.status == 'partial'


@pytest.mark.parametrize('version', ['0.18.2', '0.21.0', 'invalid'])
def test_incompatible_api_never_advertises_available(version, monkeypatch):
    """Installed-package protocol fixtures must satisfy the explicitly supported API version."""
    fake = SimpleNamespace(__version__=version, __file__=__file__, Script=lambda: None, Project=lambda: None)
    monkeypatch.setitem(sys.modules, 'jedi', fake)
    monkeypatch.setitem(sys.modules, 'parso', SimpleNamespace(__file__=__file__))
    assert python_worker.probe()['status'] == 'incompatible'


@pytest.mark.parametrize('reason', ['timeout', 'cancelled'])
def test_probe_transport_limits_are_failed_observations_not_availability(tmp_path, monkeypatch, reason):
    """The provider passes exact process limits/cancellation and retains a failed fallback outcome."""
    from atlas.semantic_providers import python as adapter
    context, _ = source_request(tmp_path)
    cancel = Event()
    if reason == 'cancelled':
        cancel.set()

    def bounded(argv, **options):
        """Protocol fixture checks the real transport boundary without needing an installed analyzer."""
        assert argv[1] == '-I' and argv[-1] == '--probe'
        assert options['cancel'] is cancel and options['timeout'] == 5
        assert options['max_output_bytes'] == 16384
        raise SemanticProcessError(reason)

    monkeypatch.setattr(adapter, 'run_bounded', bounded)
    state = JediProvider('jedi')._probe_python(Path(sys.executable), tmp_path, cancel)
    assert state.availability.status == 'failed' and state.availability.reason == reason
    assert not state.assets


def test_filtered_alternatives_keep_definition_candidate(tmp_path, monkeypatch):
    """A retained local definition is still ambiguous when another definition is outside scope."""
    from atlas.semantic_models import SemanticAvailability
    from atlas.semantic_runtime import ProbeOutcome
    from atlas.semantic_providers import python as adapter
    context, request = source_request(tmp_path)
    target = {'symbol_id': 'helper:run', 'kind': 'function', 'label': 'run',
              'source_span': {'path': 'helper.py', 'start_line': 0, 'start_column': 4,
                              'end_line': 0, 'end_column': 7, 'coordinate_kind': 'utf-16'}}
    monkeypatch.setattr(adapter, '_fingerprint', lambda assets: HASH)
    monkeypatch.setattr(adapter, 'run_bounded', lambda *args, **kwargs: SimpleNamespace(returncode=0,
        stdout=json.dumps({'results': [{'targets': [target], 'ambiguous': True}], 'reasons': ['partial-context']})))
    assets = (Path(sys.executable).resolve(), Path(adapter.WORKER).resolve(),
              Path(__file__).resolve(), Path(__file__).resolve())
    probe = ProbeOutcome(SemanticAvailability('available', engine_fingerprint=HASH, capabilities=('definition',)), assets)
    result = JediProvider('jedi').analyze(context, probe, request, Event())
    assert result.facts[0].confidence == 'candidate' and result.facts[0].kind == 'reference'
    assert result.status == 'partial' and result.coverage['definition'].total is None


def test_utf16_query_does_not_split_non_bmp_characters():
    """Jedi columns use Python codepoints while captured queries use UTF-16 code units."""
    text = '"😀"; run()\n'
    assert python_worker.column(text, 0, 6) == 5
    with pytest.raises(UnicodeDecodeError):
        python_worker.column(text, 0, 2)
