"""Synthetic production-boundary qualification, using the actual shipped native library."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from atlas.browser_api import register_browser_routes
from atlas.browser_models import BrowserCreate, BrowserQuery
from atlas.browser_privacy import BrowserPrivacy
from atlas.browser_sessions import BrowserSessions
from atlas.browser_privacy_files import private_read, private_write, PrivacyFailure
from test_browser_sessions import capture


@pytest.fixture
def native_sdk_root():
    """Require the separate SDK for tests that exercise its real masking behavior."""
    root = Path(__file__).resolve().parents[1]
    if not (root / 'vendor/ag-privacy').exists():
        pytest.skip('Optional AG Privacy SDK is not installed')
    return root


@pytest.fixture
def protected(tmp_path, native_sdk_root):
    """No real data/settings are read; temporary host keys and signed configurations are isolated."""
    gate = BrowserPrivacy(tmp_path / 'privacy')
    assert gate.status()['available'], gate.status()
    sessions = BrowserSessions()
    app = FastAPI()
    app.state.store = SimpleNamespace(data_dir=tmp_path)
    register_browser_routes(app, sessions, privacy=gate)
    try:
        with TestClient(app) as client:
            yield client, sessions, gate
    finally:
        sessions.close()


def sample():
    """Canaries cover original keys, URLs, warnings, script text and runtime attributes."""
    value = capture()
    value['page']['title'] = 'Иванов Иван SYNTHETIC_PERSON'
    value['page']['url'] = 'https://example.test/client/SYNTHETIC_PERSON'
    value['nodes'][1]['text'] = 'Иванов Иван SYNTHETIC_PERSON'
    value['nodes'][1]['attributes']['SYNTHETIC_KEY'] = 'SYNTHETIC_ATTRIBUTE'
    value['scripts'][0]['source'] += '; const password="SYNTHETIC_SECRET_987654321";'
    value['warnings'] = ['SYNTHETIC_WARNING']
    return value


def test_missing_sdk_rejects_capture_without_disabling_atlas(tmp_path, monkeypatch):
    """The source-only host answers normal requests and never retains an unprotected capture."""
    from atlas import browser_privacy_files
    from atlas.server import create_app

    monkeypatch.setattr(browser_privacy_files, 'resource_root', lambda: tmp_path)
    app = create_app(tmp_path / 'data')
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        assert client.get('/api/projects').json()['projects'] == []
        privacy = client.get('/api/browser-privacy').json()
        assert privacy['mode'] == 'blocked' and privacy['available'] is False
        rejected = client.post('/api/browser-sessions', json={'capture': sample()})
        assert rejected.status_code == 424
        assert 'SYNTHETIC' not in rejected.text
        assert client.get('/api/browser-sessions').json()['sessions'] == []


def test_default_strict_all_routes_and_ram_hold_only_masked_data(protected):
    client, sessions, gate = protected
    created = client.post('/api/browser-sessions', json={'capture': sample()})
    assert created.status_code == 200, created.text
    metadata = created.json()
    assert metadata['privacy']['mode'] == 'strict'
    assert metadata['privacy']['masked'] and metadata['privacy']['mustDiscloseMasking']
    identifier = metadata['sessionId']
    responses = [created, client.get('/api/browser-sessions'), client.get('/api/browser-sessions/' + identifier)]
    base = '/api/browser-sessions/' + identifier
    for path, body in [('query', {}), ('node', {'nodeId':'dom:n1'}), ('property', {'nodeId':'dom:n1','field':'text'}), ('source', {'scriptId':'s0'}), ('node-source', {'nodeId':'script:s0'})]:
        response = client.post(base + '/' + path, json=body)
        assert response.status_code == 200, response.text
        assert response.json()['privacy']['mustDiscloseMasking'] is True
        responses.append(response)
    retained = json.dumps(sessions.entries, ensure_ascii=False)
    for canary in ['SYNTHETIC_PERSON','SYNTHETIC_KEY','SYNTHETIC_ATTRIBUTE','SYNTHETIC_SECRET','SYNTHETIC_WARNING','PRIVATE_QUERY']:
        assert canary not in retained
        assert all(canary not in response.text for response in responses)
    assert client.get('/api/browser-privacy').json()['mode'] == 'strict'


def test_agent_cannot_change_mode_or_bypass_with_capture_flags(protected):
    client, sessions, gate = protected
    for headers in [{}, {'X-Atlas-Native-Control':'0'*64}]:
        assert client.post('/api/browser-privacy', json={'mode':'off','confirmed':True}, headers=headers).status_code == 403
    for flag in ['mode','privacy','skipPrivacy']:
        assert client.post('/api/browser-sessions', json={'capture':sample(),flag:'off'}).status_code == 422
    assert gate.status()['mode'] == 'strict'


@pytest.mark.parametrize('mode', ['credentials-only','off'])
def test_native_choice_warning_semantics_and_revocation(protected, mode):
    client, sessions, gate = protected
    old = client.post('/api/browser-sessions',json={'capture':sample()}).json()['sessionId']
    headers = {'X-Atlas-Native-Control':private_read(gate.directory / 'control.key',32).hex()}
    assert client.post('/api/browser-privacy',json={'mode':mode,'confirmed':False},headers=headers).status_code == 422
    result = client.post('/api/browser-privacy',json={'mode':mode,'confirmed':True},headers=headers)
    assert result.status_code == 200 and result.json()['mode'] == mode
    assert client.get('/api/browser-sessions/' + old).status_code == 404
    created = client.post('/api/browser-sessions',json={'capture':sample()})
    assert created.status_code == 200, created.text
    identifier = created.json()['sessionId']
    text = client.post('/api/browser-sessions/' + identifier + '/source',json={'scriptId':'s0' if mode!='off' else 's1'}).text
    assert ('SYNTHETIC_SECRET' in text) == (mode=='off')
    assert created.json()['privacy']['originalValuesAvailable'] is True
    assert client.post('/api/browser-privacy',json={'mode':'strict','confirmed':True},headers=headers).status_code == 200
    assert client.get('/api/browser-sessions/' + identifier).status_code == 404
    restarted = BrowserPrivacy(gate.directory)
    try:
        assert restarted.status()['mode'] == 'strict'
    finally:
        restarted.close()


def test_tamper_no_fallback_and_private_file_guards(protected, tmp_path):
    client, sessions, gate = protected
    profile = gate.directory / gate.state['profile'] / 'ag-privacy.runtime.json'
    profile.write_bytes(profile.read_bytes().replace(b'"strict"',b'"off"'))
    other = BrowserPrivacy(gate.directory)
    try:
        assert other.status()['mode'] == 'blocked'
        with pytest.raises(PrivacyFailure):
            with other.capture(BrowserCreate(capture=sample()).capture):
                pytest.fail('tampered profile released content')
    finally:
        other.close()
    target = tmp_path / 'target'; target.write_bytes(b'not-a-secret');target.chmod(0o600)
    link = tmp_path / 'link';link.symlink_to(target)
    with pytest.raises(OSError): private_read(link,32)
    target.chmod(0o644)
    with pytest.raises(PrivacyFailure): private_read(target,32)


def test_busy_switch_never_changes_running_snapshot(protected):
    client,sessions,gate=protected
    headers={'X-Atlas-Native-Control':private_read(gate.directory/'control.key',32).hex()}
    sessions.admission.acquire()
    try:
        assert client.post('/api/browser-privacy',json={'mode':'off','confirmed':True},headers=headers).status_code==503
        assert gate.status()['mode']=='strict'
    finally:
        sessions.admission.release()


def test_installation_credential_is_private_and_exact(tmp_path):
    """The product installer copies only synthetic host credentials and never selects a mode."""
    import importlib.util
    tool=Path(__file__).resolve().parents[1]/'tools/install_browser_privacy_control.py'
    spec=importlib.util.spec_from_file_location('browser_privacy_installer_fixture',tool)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    home=tmp_path/'home';container=home/'Library/Containers/com.code-atlas.browser.Extension/Data'
    container.mkdir(parents=True)
    data=tmp_path/'data';(data/'browser-privacy').mkdir(parents=True)
    private_write(data/'browser-privacy/control.key',b'x'*32)
    assert module.install(data,home)=={'installed':True,'secretPrinted':False}
    target=container/'Library/Application Support/Atlas Browser/native-control.key'
    assert private_read(target,32)==b'x'*32
    assert target.stat().st_mode&0o077==0
    target.unlink();target.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(PrivacyFailure):module.install(data,home)


def test_signed_mode_persists_and_capture_aliases_are_deterministic(protected):
    client,sessions,gate=protected
    results=[client.post('/api/browser-sessions',json={'capture':sample()}).json() for _ in range(2)]
    texts=[]
    for metadata in results:
        texts.append(client.post('/api/browser-sessions/'+metadata['sessionId']+'/property',json={'nodeId':'dom:n1','field':'text'}).json()['text'])
    assert texts[0]==texts[1] and 'SYNTHETIC' not in texts[0]
    headers={'X-Atlas-Native-Control':private_read(gate.directory/'control.key',32).hex()}
    assert client.post('/api/browser-privacy',json={'mode':'off','confirmed':1},headers=headers).status_code==422
    assert client.post('/api/browser-privacy',json={'mode':'credentials-only','confirmed':True},headers=headers).status_code==200
    other=BrowserPrivacy(gate.directory)
    try:assert other.status()['mode']=='credentials-only'
    finally:other.close()


def test_native_refusal_requires_os_confirmation_before_key_read(monkeypatch):
    """A scripted extension click alone cannot reduce protection through the native host."""
    from atlas import browser_native_host as host
    from atlas import browser_human_auth as auth
    monkeypatch.setattr(auth,'confirm_privacy_change',lambda:False)
    with pytest.raises(auth.HumanConfirmationRequired):
        host.forward_message({'operation':'privacy-configure','mode':'off','confirmed':True},8765)


def test_human_helper_cancellation_timeout_and_exact_arguments(monkeypatch):
    """Synthetic runner only: this test never opens a Touch ID/password prompt."""
    import subprocess
    from atlas import browser_human_auth as auth
    calls=[]
    def cancel(arguments,**kwargs):
        calls.append(arguments);assert len(arguments)==1;assert kwargs['timeout']==22
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(auth.subprocess,'run',cancel)
    assert auth.confirm_privacy_change() is False
    assert len(calls)==1
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('synthetic-auth',22)
    monkeypatch.setattr(auth.subprocess,'run',timeout)
    assert auth.confirm_privacy_change() is False


def test_unverified_python_bytecode_cache_cannot_replace_verified_adapter(tmp_path, monkeypatch, native_sdk_root):
    """A forged timestamp-valid pyc is ignored even though the approved source file is unchanged."""
    import importlib.util
    import marshal
    import shutil
    import struct
    from atlas import browser_privacy_files as files
    destination=tmp_path/'vendor/ag-privacy'
    shutil.copytree(native_sdk_root/'vendor/ag-privacy',destination,ignore=shutil.ignore_patterns('__pycache__'))
    source=destination/'adapter.py'
    info=source.stat()
    bytecode=Path(importlib.util.cache_from_source(str(source)));bytecode.parent.mkdir(exist_ok=True)
    forged=compile("raise RuntimeError('synthetic-unverified-bytecode-executed')",str(source),'exec')
    header=importlib.util.MAGIC_NUMBER+struct.pack('<III',0,int(info.st_mtime),info.st_size)
    bytecode.write_bytes(header+marshal.dumps(forged))
    # Prove that this fixture would compromise an ordinary filename loader.
    spec=importlib.util.spec_from_file_location('unverified_adapter_fixture',source)
    unsafe=importlib.util.module_from_spec(spec)
    with pytest.raises(RuntimeError,match='synthetic-unverified-bytecode-executed'):
        spec.loader.exec_module(unsafe)
    monkeypatch.setattr(files,'resource_root',lambda:tmp_path)
    _,_,adapter,_,_=files.sdk_files()
    assert adapter.__name__=='VerifiedAgPrivacy'


def test_packed_native_data_loads_one_verified_private_copy(tmp_path, monkeypatch, native_sdk_root):
    """App data archives preserve SDK hashes without requiring unsigned nested Mach-O signatures."""
    import importlib.util
    from atlas import browser_privacy_files as files
    root=native_sdk_root
    spec=importlib.util.spec_from_file_location('browser_privacy_pack_runtime_fixture',root/'packaging/browser_privacy_pack.py')
    pack=importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)
    destination=tmp_path/'vendor/ag-privacy';destination.mkdir(parents=True)
    for name,data in pack.prepared_privacy_data(root).items():
        path=destination/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    monkeypatch.setattr(files,'resource_root',lambda:tmp_path)
    _,first,adapter,_,_=files.sdk_files()
    _,second,_,_,_=files.sdk_files()
    assert first==second and not first.is_relative_to(destination)
    assert first.stat().st_mode&0o077==0
    gate=BrowserPrivacy(tmp_path/'installation')
    try:
        assert gate.status()['mode']=='strict'
        with gate.capture(BrowserCreate(capture=sample()).capture) as (cleaned,status):
            assert status['masked'] and 'SYNTHETIC' not in cleaned.model_dump_json()
    finally:gate.close()
