"""GUI-safe Node discovery and environment identity without shell or install side effects."""
from pathlib import Path
import os
from threading import Event
from types import SimpleNamespace

import pytest

from atlas import semantic_engine_discovery as discovery
from atlas import semantic_environment as environment
from atlas import semantic_process as process
from atlas.semantic_models import SemanticAvailability, SemanticContext
from atlas.semantic_providers import typescript
from atlas.semantic_runtime import DISCOVERY_TTL, ProbeOutcome, SemanticRuntime


def executable(path):
    """Create an executable fixture whose presence is checked but never launched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o700)
    return path


def context(root):
    """Pin an isolated workspace identity independent of the host's user projects."""
    return SemanticContext('fixture-snapshot', 'fixture-workspace', str(root),
                           'fixture-target', 'a' * 64, {})


def test_path_choice_precedes_fixed_node_installations(tmp_path, monkeypatch):
    """Keep an existing PATH choice even when a fixed installation is also present."""
    selected = executable(tmp_path / 'path/bin' / ('node.exe' if os.name == 'nt' else 'node'))
    fallback = executable(tmp_path / 'fixed/bin/node')
    monkeypatch.setattr(process, '_node_installation_candidates', lambda: (fallback,))
    assert process.discover_node_executable(search_path=str(selected.parent)) == selected


def test_gui_path_uses_only_known_fallback_without_running_it(tmp_path, monkeypatch):
    """An empty GUI PATH can find a fixed executable through presence checks only."""
    node = executable(tmp_path / 'fixed/bin/node')
    monkeypatch.setattr(process, '_node_installation_candidates', lambda: (tmp_path / 'missing', node))
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Presence discovery ran code'))
    assert process.discover_node_executable(search_path=str(tmp_path / 'empty')) == node


@pytest.mark.parametrize('invalid', ['missing', 'directory', 'nonexecutable'])
def test_invalid_explicit_node_never_falls_back(tmp_path, monkeypatch, invalid):
    """Missing, non-file and POSIX non-executable preferences remain explicit failures."""
    if invalid == 'nonexecutable' and os.name == 'nt':
        pytest.skip('Windows executable permission is not a POSIX mode-bit contract')
    fallback = executable(tmp_path / 'fallback/node')
    selected = tmp_path / invalid
    if invalid == 'directory':
        selected.mkdir()
    elif invalid == 'nonexecutable':
        selected.write_text('not executable')
        selected.chmod(0o600)
    monkeypatch.setattr(process, '_node_installation_candidates', lambda: pytest.fail('Explicit fallback'))
    assert process.discover_node_executable(explicit=selected, search_path=str(fallback.parent)) is None


def test_posix_and_windows_candidates_are_fixed_and_bounded(tmp_path, monkeypatch):
    """Mock platform roots without claiming native Windows process qualification."""
    monkeypatch.setattr(process, 'sys', SimpleNamespace(platform='darwin'))
    assert process._node_installation_candidates() == (Path('/usr/local/bin/node'), Path('/opt/homebrew/bin/node'))
    monkeypatch.setattr(process, 'sys', SimpleNamespace(platform='win32'))
    roots = (tmp_path / 'Program Files64', tmp_path / 'Program Files32')
    monkeypatch.setenv('ProgramW6432', str(roots[0]))
    monkeypatch.setenv('ProgramFiles', str(roots[1]))
    assert process._node_installation_candidates() == tuple(root / 'nodejs/node.exe' for root in roots)
    monkeypatch.setenv('ProgramFiles', str(roots[0]))
    assert process._node_installation_candidates() == (roots[0] / 'nodejs/node.exe',)


def test_diagnostics_and_typescript_provider_share_gui_node_choice(tmp_path, monkeypatch):
    """The displayed candidate must be the interpreter actually selected for probing."""
    node = executable(tmp_path / 'prefix/bin/node')
    compiler = tmp_path / 'prefix/lib/node_modules/typescript/lib/typescript.js'
    compiler.parent.mkdir(parents=True)
    compiler.write_text('fixture compiler bytes')
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    monkeypatch.delenv('ATLAS_TYPESCRIPT_PATH', raising=False)
    monkeypatch.setattr(process, '_node_installation_candidates', lambda: (node,))
    seen = []

    def compatible_probe(command, **kwargs):
        """Capture the selected interpreter/compiler without executing the fixture."""
        seen.append(command)
        return process.ProcessResult(0, b'{"version":"5.9.2","supported":true}', b'')

    monkeypatch.setattr(typescript, 'run_bounded', compatible_probe)
    assert discovery._typescript(tmp_path) == {'nodePath': [str(node)], 'enginePath': [str(compiler)]}
    result = typescript.TypeScriptProvider('fixture-typescript').probe(context(tmp_path), Event())
    assert result.availability.status == 'available'
    assert seen == [[str(node), str(typescript.WORKER), str(compiler), '--probe']]
    assert result.assets[:2] == (node, compiler)


@pytest.mark.parametrize('variable', (
    'PATH', 'PATHEXT', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
    'ProgramFiles', 'ProgramW6432', 'DOTNET_ROOT', 'JAVA_HOME', 'GOROOT',
    'GOPATH', 'SDKROOT', 'DEVELOPER_DIR', 'TOOLCHAINS', 'RUSTUP_HOME',
    'CARGO_HOME', 'ATLAS_JEDI_PYTHON', 'ATLAS_TYPESCRIPT_PATH',
))
def test_provider_environment_choices_invalidate_identity(monkeypatch, variable):
    """Changing each supported choice invalidates identity without returning its value."""
    before = environment.discovery_environment_fingerprint()
    monkeypatch.setenv(variable, 'fixture-environment-value')
    after = environment.discovery_environment_fingerprint()
    assert before != after
    assert len(after) == 64 and set(after) <= set('0123456789abcdef')
    assert 'fixture-environment-value' not in after


def test_unrelated_environment_does_not_invalidate_or_leak(monkeypatch):
    """Unrelated host values do not broaden cache invalidation or enter its digest."""
    before = environment.discovery_environment_fingerprint()
    monkeypatch.setenv('ATLAS_SCOUT_UNRELATED_SECRET', 'private-unrelated-value')
    assert environment.discovery_environment_fingerprint() == before


@pytest.mark.parametrize('attribute,value', [('executable', '/fixture/python'), ('prefix', '/fixture/prefix'),
                                          ('base_prefix', '/fixture/base'), ('frozen', True),
                                          ('_MEIPASS', '/fixture/bundle')])
def test_python_host_identity_invalidation(monkeypatch, attribute, value):
    """A different interpreter, venv or frozen bundle must not share discovery identity."""
    before = environment.discovery_environment_fingerprint()
    monkeypatch.setattr(environment.sys, attribute, value, raising=False)
    assert environment.discovery_environment_fingerprint() != before


def test_negative_discovery_keeps_ttl_but_environment_change_rechecks(tmp_path, monkeypatch):
    """Retain the no-polling TTL while avoiding a stale result from another environment."""
    class MissingProvider:
        """Count real runtime probe transitions without starting a semantic engine."""
        calls = 0

        def probe(self, selected, cancel):
            """Return a terminal missing result to exercise negative discovery caching."""
            self.calls += 1
            return ProbeOutcome(SemanticAvailability('missing', 'fixture-missing'))

    provider = MissingProvider()
    runtime = SemanticRuntime(tmp_path / 'cache', {'fixture': provider}, clock=lambda: 100.0)
    selected = context(tmp_path)
    monkeypatch.setenv('PATH', '/fixture/gui')
    try:
        runtime.on_use(selected, ['fixture'])
        runtime.wait_for_idle(timeout=5)
        assert runtime.status(selected, ['fixture'])['fixture'].next_check_at == 100.0 + DISCOVERY_TTL
        runtime.on_use(selected, ['fixture'])
        runtime.wait_for_idle(timeout=5)
        assert provider.calls == 1
        monkeypatch.setenv('PATH', '/fixture/shell')
        assert runtime.status(selected, ['fixture'])['fixture'].status == 'unchecked'
        runtime.on_use(selected, ['fixture'])
        runtime.wait_for_idle(timeout=5)
        assert provider.calls == 2
    finally:
        runtime.close()
