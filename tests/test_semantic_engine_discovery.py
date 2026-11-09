"""Host diagnostics are reversible, presence-only and discoverable without an agent process."""
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
import pytest

from atlas import server, store
from atlas.semantic_engine_discovery import engine_diagnostics
from atlas.semantic_engine_settings import SemanticEngineSettings
from atlas.semantic_engine_mcp import register_semantic_engine_tools
from test_semantic_overlay import semantic_project


def test_patch_field_reset_preserves_other_overrides_and_mods(tmp_path):
    """A field reset merges atomically; a complete reset removes only the selected profile."""
    settings = SemanticEngineSettings(tmp_path)
    settings.save('ts', 'typescript-local-v1', {'enginePath': '/typescript.js', 'nodePath': '/node'})
    settings.save('py', 'jedi-local-v1', {'pythonPath': '/python'})
    assert settings.save('ts', 'typescript-local-v1', {'enginePath': ''}, mode='patch') == {'nodePath': '/node'}
    assert settings.save('ts', 'typescript-local-v1', {}, mode='patch') == {'nodePath': '/node'}
    assert settings.save('ts', 'typescript-local-v1', {}) == {}
    assert settings.read() == {'py': {'pythonPath': '/python'}}
    with pytest.raises(ValueError):
        settings.save('py', 'jedi-local-v1', {'command': '/bin/sh'}, mode='patch')
    assert settings.read() == {'py': {'pythonPath': '/python'}}


def test_presence_diagnostics_do_not_probe_or_mutate_explicit_override(semantic_project, monkeypatch):
    """A found venv path is an unverified candidate and never replaces a broken explicit choice."""
    import subprocess
    state, project_id, _, root, *_ = semantic_project
    settings = SemanticEngineSettings(state.data_dir)
    settings.save('semantic-python', 'jedi-local-v1', {'pythonPath': '/missing/explicit-python'})
    candidate = root / '.venv/bin/python'
    candidate.parent.mkdir(parents=True)
    candidate.write_text('presence fixture; do not execute')
    before = settings.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('Presence diagnostics must not start a process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = engine_diagnostics(state, settings, 'semantic-python', project_id)
    assert result['paths'] == {'pythonPath': '/missing/explicit-python'}
    assert result['candidates']['pythonPath'][0] == str(candidate)
    assert result['candidateStatus'] == 'presence-only' and result['compatibility'] == 'unchecked'
    assert result['fieldModes'] == {'pythonPath': 'explicit'}
    assert result['scope'] == 'host-global' and result['autoInstall'] is False
    assert len(result['candidates']['pythonPath']) <= 3
    assert settings.path.read_bytes() == before
    profiles = [item for item in state.mods.catalog()['mods']
                if item.get('manifest', {}).get('analysis', {}).get('semanticProviders')]
    assert len(profiles) == 11
    for item in profiles:
        observed = engine_diagnostics(state, settings, item['id'], project_id)
        assert observed['compatibility'] == 'unchecked'
        assert all(len(paths) <= 3 for paths in observed['candidates'].values())


def test_roslyn_presence_candidates_use_numeric_stable_sdk_order(tmp_path, monkeypatch):
    """SDK10 sorts ahead of8; preview folders do not silently suggest a different selection policy."""
    from atlas import semantic_engine_discovery as discovery
    dotnet = tmp_path / 'dotnet'
    dotnet.write_text('not executed')
    for version in ('8.0.403', '10.0.102', '9.0.200-preview.0'):
        directory = tmp_path / 'sdk' / version / 'Roslyn/bincore'
        directory.mkdir(parents=True)
        for name in ('Microsoft.CodeAnalysis.dll', 'Microsoft.CodeAnalysis.CSharp.dll'):
            (directory / name).write_text('presence only')
    monkeypatch.setattr(discovery, 'discover_executable', lambda *args, **kwargs: dotnet)
    result = discovery._roslyn()
    assert [Path(path).name for path in result['sdkPath']] == ['10.0.102', '8.0.403']


def test_browser_http_discovery_contract_and_patch_reset(semantic_project):
    """A browser-page agent can inspect, discover, configure and undo using same-origin API."""
    state, project_id, _, _, *_ = semantic_project
    store.write_json(state.registry, state.projects)
    app = server.create_app(state.data_dir)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        config = client.get('/api/semantic-engines/settings').json()
        assert config['configuration']['scope'] == 'host-global'
        assert any(item['modId'] == 'semantic-python' for item in config['profiles'])
        base = '/api/mods/semantic-typescript/semantic-engine-settings'
        response = client.post(base, json={'paths': {'enginePath': '/engine.js', 'nodePath': '/node'}})
        assert response.status_code == 200
        response = client.post(base, json={'paths': {'enginePath': ''}, 'mode': 'patch'})
        assert response.json()['paths'] == {'nodePath': '/node'}
        assert client.post(base, json={'paths': {}, 'mode': 'replace'}).json()['mode'] == 'automatic'
        diagnostics = '/api/mods/semantic-python/semantic-engine-diagnostics'
        result = client.get(diagnostics, params={'projectId': project_id})
        assert result.status_code == 200 and result.json()['projectId'] == project_id
        assert client.get(diagnostics, params={'projectId': 'unknown-project'}).status_code == 404
        assert client.get('/api/mods/not-a-mod/semantic-engine-diagnostics').status_code == 404
        assert client.get(diagnostics, headers={'Origin': 'https://outside.example'}).status_code == 403
        assert client.post(base, json={'paths': {}, 'mode': 'execute'}).status_code == 422
        assert client.post(base, json={'paths': {'nodePath': 'node --eval evil'}, 'mode': 'patch'}).status_code == 400
        assert client.get('/api/agent-setup').json()['semanticEngineConfiguration']['scope'] == 'host-global'
        guide = client.get('/agent-setup.md').text
        assert '## Local semantic engines' in guide and 'presence does not prove compatibility' in guide
        assert 'inspect_semantic_engine_settings' in guide and 'same-origin API' in guide
        html = client.get('/mods.html').text
        assert 'engine-agent-configuration' in html and '/agent-setup.md' in html
        browser_guide = client.get('/engine-setup.html')
        assert browser_guide.status_code == 200
        assert 'text/html' in browser_guide.headers['content-type']
        assert 'Самоанализ и HTTP API' in browser_guide.text and '"mode":"patch"' in browser_guide.text


def test_mcp_tools_proxy_diagnostics_and_partial_updates_with_project_scope():
    """MCP uses the Atlas host and rejects a foreign bound project before HTTP work."""
    calls = []
    async def request(method, url, body=None):
        calls.append((method, url, body))
        return {'scope': 'host-global'}
    def selected(requested):
        if requested not in {None, 'project'}:
            raise ValueError('foreign project')
        return 'project'
    mcp = FastMCP('semantic settings test')
    register_semantic_engine_tools(mcp, request, selected, 'project')
    async def exercise():
        await mcp.call_tool('inspect_semantic_engine_settings', {'modId': 'semantic-python'})
        assert calls[-1] == ('GET', '/api/mods/semantic-python/semantic-engine-diagnostics?projectId=project', None)
        await mcp.call_tool('set_semantic_engine_paths', {'modId': 'semantic-python', 'paths': {'pythonPath': ''}})
        assert calls[-1] == ('POST', '/api/mods/semantic-python/semantic-engine-settings',
                             {'paths': {'pythonPath': ''}, 'mode': 'patch'})
        before = len(calls)
        with pytest.raises(Exception, match='foreign project'):
            await mcp.call_tool('inspect_semantic_engine_settings', {'modId': 'semantic-python', 'projectId': 'other'})
        assert len(calls) == before
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        assert tools['inspect_semantic_engine_settings'].annotations.readOnlyHint is True
        assert tools['set_semantic_engine_paths'].annotations.readOnlyHint is False
    asyncio.run(exercise())


def test_merged_settings_budget_never_corrupts_later_read_or_reset(tmp_path):
    """Unicode patches may exceed the total byte budget even when every field is valid."""
    from atlas.semantic_engine_settings import FIELDS, MAX_BYTES
    settings = SemanticEngineSettings(tmp_path)
    accepted, rejected = [], False
    for strategy, names in FIELDS.items():
        identifier = strategy.removesuffix('-local-v1')
        for name in names:
            before = settings.path.read_bytes() if settings.path.exists() else None
            try:
                settings.save(identifier, strategy, {name: '/' + 'я' * 4095}, mode='patch')
            except ValueError as error:
                assert 'общий размер' in str(error)
                assert settings.path.read_bytes() == before
                rejected = True
                break
            assert settings.path.stat().st_size <= MAX_BYTES
            assert settings.read()[identifier][name].startswith('/')
            accepted.append((identifier, strategy))
        if rejected:
            break
    assert rejected and accepted
    identifier, strategy = accepted[-1]
    assert settings.save(identifier, strategy, {}) == {}
    assert identifier not in settings.read()


def test_expanded_path_and_profile_count_are_validated_before_write(tmp_path, monkeypatch):
    """Accepted data must remain readable after home expansion and cumulative profile additions."""
    from atlas.semantic_engine_settings import MAX_MODS
    settings = SemanticEngineSettings(tmp_path)
    monkeypatch.setenv('HOME', '/expanded-home')
    with pytest.raises(ValueError, match='Развёрнутый путь'):
        settings.save('py', 'jedi-local-v1', {'pythonPath': '~/' + 'a' * 4094})
    assert settings.read() == {}
    for index in range(MAX_MODS):
        settings.save('py' + str(index), 'jedi-local-v1', {'pythonPath': '/python'})
    before = settings.path.read_bytes()
    with pytest.raises(ValueError, match='общий размер'):
        settings.save('one-too-many', 'jedi-local-v1', {'pythonPath': '/python'})
    assert settings.path.read_bytes() == before and len(settings.read()) == MAX_MODS
